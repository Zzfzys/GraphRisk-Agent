"""Bounded, label-free disk queries shared by historical and new-event graphs."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
from pydantic import Field, field_validator, model_validator

from graphrisk.graph.schema import GraphTransaction, assemble_ego_graph, ENTITY_FIELDS
from graphrisk.graph.build_artifacts import GRAPH_COLUMNS
from graphrisk.investigation.contracts import StrictModel, TransactionId
from graphrisk.features.wide_contract import ENTITY_KEYS, FORBIDDEN


class NewTransaction(StrictModel):
    transaction_id: TransactionId
    source_transaction_dt_seconds: int = Field(strict=True, ge=0, le=2147483647)
    transaction_amount: float = Field(strict=True, ge=0, allow_inf_nan=False)
    card_id: str = Field(min_length=1, max_length=256)
    device_id: str = Field(min_length=1, max_length=256)
    email_domain: str = Field(min_length=1, max_length=256)
    address_id: str = Field(min_length=1, max_length=256)
    product_code: str = Field(min_length=1, max_length=32)
    has_identity: bool = Field(default=False, strict=True)
    raw_features: dict[str, str | float | None] = Field(default_factory=dict, max_length=431)

    @field_validator("raw_features", mode='before')
    @classmethod
    def no_derived_or_labels(cls, value):
        if not isinstance(value, dict) or any(not isinstance(k,str) or isinstance(v,bool)
                or not isinstance(v,(str,int,float,type(None))) for k,v in value.items()):
            raise ValueError('raw features require scalar strings, numbers or null')
        if any(n.casefold() in FORBIDDEN or n.startswith(("hist_", "relative_"))
               or n in {"has_identity", "transaction_id", "source_transaction_dt_seconds"} for n in value):
            raise ValueError("raw features cannot supply labels, scores or derived statistics")
        if any(isinstance(v, float) and not math.isfinite(v) for v in value.values()):
            raise ValueError("raw numeric values must be finite or null")
        return value

    @model_validator(mode='after')
    def entity_consistency(self):
        for field,prefix in (('card_id','unknown-card-'),('device_id','unknown-device-'),
                ('email_domain','unknown-email-'),('address_id','unknown-address-')):
            value=getattr(self,field)
            if value != value.strip() or (value.lower().startswith('unknown-') and value != prefix+self.transaction_id):
                raise ValueError('missing entity markers must be scoped to this event ID')
        raw=self.raw_features
        def token(key):
            value=raw.get(key)
            return None if value is None or not str(value).strip() else str(value).strip().lower()
        groups=[('card_id',[f'card{i}' for i in range(1,7)],
            lambda:'card-'+':'.join(token(f'card{i}') or 'na' for i in range(1,7)) if token('card1') else 'unknown-card-'+self.transaction_id),
            ('address_id',['addr1','addr2'],lambda:'address-'+str(token('addr1'))+':'+(token('addr2') or 'na') if token('addr1') else 'unknown-address-'+self.transaction_id),
            ('device_id',['DeviceInfo','DeviceType'],lambda:'device-'+str(token('DeviceInfo') or token('DeviceType')) if token('DeviceInfo') or token('DeviceType') else 'unknown-device-'+self.transaction_id),
            ('email_domain',['P_emaildomain','R_emaildomain'],lambda:token('P_emaildomain') or token('R_emaildomain') or 'unknown-email-'+self.transaction_id)]
        for field,names,derive in groups:
            if all(n in raw for n in names) and getattr(self,field)!=derive():
                raise ValueError('raw fields contradict canonical event entities')
        return self


class DiskHistory:
    """Keep Parquet on disk; SQL projects no outcome columns or full Python index."""
    def __init__(self, canonical: Path, features: Path, scratch: Path, *, calendar_timezone='UTC'):
        self.canonical, self.features, self.scratch = map(Path, (canonical, features, scratch))
        self.calendar_timezone=ZoneInfo(calendar_timezone)

    @contextmanager
    def connect(self):
        self.scratch.mkdir(parents=True, exist_ok=True)
        with duckdb.connect() as db:
            db.execute("SET threads=1")
            db.execute("SET memory_limit='128MB'")
            db.execute("SET TimeZone='UTC'")
            db.execute("SET temp_directory=?", [str(self.scratch)])
            yield db

    def graph_row(self, key):
        with self.connect() as db:
            rows = db.execute("SELECT "+",".join(GRAPH_COLUMNS)+" FROM read_parquet(?) WHERE transaction_id=?",
                              [str(self.features), key]).fetchall()
        if len(rows) != 1:
            raise ValueError("graph target missing or duplicate")
        return dict(zip(GRAPH_COLUMNS, rows[0], strict=True))

    def selected_history(self, row, *, cap=5):
        if isinstance(cap, bool) or not 1 <= cap <= 50:
            raise ValueError("bounded history cap required")
        selected = {}
        with self.connect() as db:
            for field, _ in ENTITY_FIELDS:
                key = row[field]
                if key.lower().startswith("unknown-"):
                    continue
                rows = db.execute("SELECT "+",".join(GRAPH_COLUMNS)+" FROM read_parquet(?) WHERE "+field+
                    "=? AND transaction_time<? ORDER BY transaction_time DESC,transaction_id ASC LIMIT ?",
                    [str(self.features), key, row["transaction_time"], cap]).fetchall()
                for values in rows:
                    record = dict(zip(GRAPH_COLUMNS, values, strict=True))
                    if record["transaction_time"] >= row["transaction_time"]:
                        raise ValueError("historical query returned simultaneous or future event")
                    selected[record["transaction_id"]] = record
        return list(selected.values())

    def graph(self, row):
        return assemble_ego_graph(GraphTransaction.from_feature_row(row),
            [GraphTransaction.from_feature_row(v) for v in self.selected_history(row)])

    def new_features(self, event: NewTransaction, *, columns, raw_names):
        """Recompute compact and wide history at the supplied event time, never fit."""
        event=NewTransaction.model_validate(event.model_dump())
        if set(event.raw_features)-set(raw_names):
            raise ValueError("unregistered raw feature name")
        stamp = event.source_transaction_dt_seconds
        row = event.model_dump(exclude={"raw_features", "has_identity"})
        row["transaction_time"] = datetime(2000, 1, 1, tzinfo=timezone.utc)+timedelta(seconds=stamp)
        calendar_time=row['transaction_time'].astimezone(self.calendar_timezone)
        identity = hashlib.sha256((event.card_id+"|"+event.address_id+"|"+event.email_domain).encode()).hexdigest()
        raw = dict(event.raw_features)
        # Named current-event fields have a single source of truth.
        for key, val in (("TransactionAmt", event.transaction_amount), ("ProductCD", event.product_code)):
            if key in raw and raw[key] != val:
                raise ValueError("raw/current event amount or product mismatch")
            raw[key] = val
        derived = {"has_identity":float(event.has_identity), "relative_day":float(stamp//86400),
            "relative_day_sin":math.sin(2*math.pi*(stamp%86400)/86400),
            "relative_day_cos":math.cos(2*math.pi*(stamp%86400)/86400)}
        with self.connect() as db:
            if db.execute("SELECT count(*) FROM read_parquet(?) WHERE transaction_id=?",
                          [str(self.canonical), event.transaction_id]).fetchone()[0]:
                raise ValueError("new event ID already exists")
            counts=[]
            for hours in (1,24,168):
                counts.append(db.execute("SELECT count(*) FROM read_parquet(?) WHERE identity_key=? "
                    "AND source_transaction_dt_seconds>=? AND source_transaction_dt_seconds<?",
                    [str(self.canonical), identity, stamp-hours*3600, stamp]).fetchone()[0])
            avg,std = db.execute("SELECT avg(transaction_amount),stddev_pop(transaction_amount) "
                "FROM read_parquet(?) WHERE identity_key=? AND source_transaction_dt_seconds>=? "
                "AND source_transaction_dt_seconds<?",[str(self.canonical), identity, stamp-86400,stamp]).fetchone()
            row.update(tx_count_last_1h=counts[0],tx_count_last_24h=counts[1],tx_count_last_7d=counts[2],
                amount_mean_last_24h=avg,amount_std_last_24h=std,
                amount_deviation_last_24h=None if avg is None else event.transaction_amount-avg,
                transaction_hour=calendar_time.hour,
                transaction_day_of_week=(calendar_time.weekday()+1)%7)
            for entity, field in ENTITY_KEYS.items():
                # Unknown placeholders are event-specific, matching the canonical adapter.
                key = getattr(event, field)
                n,mean,spread = db.execute("SELECT count(*),avg(transaction_amount),stddev_pop(transaction_amount) "
                    "FROM read_parquet(?) WHERE "+field+"=? AND source_transaction_dt_seconds>=? "
                    "AND source_transaction_dt_seconds<?",[str(self.canonical),key,stamp-86400,stamp]).fetchone()
                previous,first = db.execute("SELECT max(source_transaction_dt_seconds),min(source_transaction_dt_seconds) "
                    "FROM read_parquet(?) WHERE "+field+"=? AND source_transaction_dt_seconds<?",
                    [str(self.canonical),key,stamp]).fetchone()
                values=(n,mean,spread,None if previous is None else stamp-previous,None if first is None else stamp-first)
                for suffix,value in zip(("count_24h","amount_mean_24h","amount_std_24h","seconds_since_previous","age_seconds"),values):
                    derived["hist_"+entity+"_"+suffix]=value
        values={**{k:raw.get(k) for k in raw_names},**derived}
        if set(columns)-set(values):
            raise ValueError("required model feature has no declared source")
        return row,{k:values[k] for k in columns}
