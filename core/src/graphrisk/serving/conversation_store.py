"""Isolated conversation documents; never updates investigation/approval tables."""
from copy import deepcopy
from threading import RLock

from graphrisk.agent.upgrade_contract import canonical, digest
from graphrisk.serving.conversations import ConversationError


class MemoryConversationStore:
    mode = 'memory'

    def __init__(self):
        self.values, self.keys, self.lock = {}, {}, RLock()

    def create(self, key, fingerprint, value):
        with self.lock:
            hashed = digest(key)
            if hashed in self.keys:
                previous, identifier = self.keys[hashed]
                if previous != fingerprint:
                    raise ConversationError('idempotency_conflict')
                return deepcopy(self.values[identifier])
            identifier = value['conversation_id']
            self.keys[hashed] = fingerprint, identifier
            self.values[identifier] = deepcopy(value)
            return deepcopy(value)

    def get(self, identifier):
        with self.lock:
            if identifier not in self.values:
                raise ConversationError('conversation_not_found')
            return deepcopy(self.values[identifier])

    def mutate(self, identifier, fn):
        with self.lock:
            value, result = fn(self.get(identifier))
            self.values[identifier] = deepcopy(value)
            return result


class PostgresConversationStore:
    mode = 'postgres'

    def __init__(self, dsn):
        from graphrisk.serving.postgres_store import PostgresStore
        self.database = PostgresStore(dsn)

    def migrate(self):
        with self.database.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(7017, 44)')
            db.execute('''CREATE TABLE IF NOT EXISTS agent_conversations_v1 (
                conversation_id text PRIMARY KEY,
                create_key_sha text UNIQUE NOT NULL,
                fingerprint text NOT NULL,
                content jsonb NOT NULL,
                content_sha text NOT NULL)''')

    @staticmethod
    def _value(row):
        if row is None:
            raise ConversationError('conversation_not_found')
        value = row['content']
        if digest(value) != row['content_sha']:
            raise ConversationError('conversation_content_changed')
        return value

    def create(self, key, fingerprint, value):
        from psycopg.types.json import Jsonb
        with self.database.connect() as db:
            db.execute('''INSERT INTO agent_conversations_v1 VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT(create_key_sha) DO NOTHING''',
                       (value['conversation_id'], digest(key), fingerprint, Jsonb(value), digest(value)))
            row = db.execute('SELECT * FROM agent_conversations_v1 WHERE create_key_sha=%s', (digest(key),)).fetchone()
            if row['fingerprint'] != fingerprint:
                raise ConversationError('idempotency_conflict')
            return self._value(row)

    def get(self, identifier):
        with self.database.connect() as db:
            return self._value(db.execute('SELECT * FROM agent_conversations_v1 WHERE conversation_id=%s', (identifier,)).fetchone())

    def mutate(self, identifier, fn):
        from psycopg.types.json import Jsonb
        with self.database.connect() as db:
            row = db.execute('SELECT * FROM agent_conversations_v1 WHERE conversation_id=%s FOR UPDATE', (identifier,)).fetchone()
            value, result = fn(self._value(row))
            db.execute('UPDATE agent_conversations_v1 SET content=%s,content_sha=%s WHERE conversation_id=%s',
                       (Jsonb(value), digest(value), identifier))
            return result
