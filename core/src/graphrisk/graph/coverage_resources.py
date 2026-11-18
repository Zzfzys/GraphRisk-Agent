"""CPU inference budgets, independent of the unchanged training contract."""
import json
from pathlib import Path
import threading
import time

import psutil


RUNTIME = dict(minimum_available_ram_gib=1.5, maximum_process_rss_mib=1280,
               threads=1, duckdb_memory_mib=128, graph_batch=64, maximum_seconds=7200)


class CoverageMonitor:
    def __init__(self, path, *, maximum_rss_mib=1280, maximum_seconds=7200):
        self.path = Path(path)
        self.maximum_rss_mib, self.maximum_seconds = maximum_rss_mib, maximum_seconds
        self.started = time.perf_counter()
        self.samples = []
        self.stop = threading.Event()
        self.violation = threading.Event()
        self.process = psutil.Process()

    def sample(self):
        info = self.process.memory_info()
        sample = dict(seconds=time.perf_counter()-self.started, rss_mib=info.rss/2**20,
                      peak_rss_mib=getattr(info, 'peak_wset', info.rss)/2**20,
                      available_ram_gib=psutil.virtual_memory().available/2**30)
        self.samples.append(sample)
        if (sample['peak_rss_mib'] > self.maximum_rss_mib or
                sample['available_ram_gib'] < RUNTIME['minimum_available_ram_gib'] or
                sample['seconds'] > self.maximum_seconds):
            self.violation.set()
        return sample

    def require(self):
        self.sample()
        if self.violation.is_set():
            self.path.parent.mkdir(parents=True,exist_ok=True)
            self.path.write_text(json.dumps(dict(budget=RUNTIME,samples=self.samples,status='failed'),indent=2),encoding='utf-8')
            raise MemoryError('coverage resource envelope reached; checkpoint preserved')

    def loop(self):
        while not self.stop.wait(1):
            self.sample()

    def __enter__(self):
        self.require()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, kind, error, traceback):
        self.stop.set()
        self.thread.join(timeout=5)
        self.sample()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(dict(budget={**RUNTIME, 'maximum_process_rss_mib':self.maximum_rss_mib,
            'maximum_seconds':self.maximum_seconds}, samples=self.samples,
            status='failed' if kind or self.violation.is_set() else 'passed'), indent=2), encoding='utf-8')
        if kind is None and self.violation.is_set():
            raise MemoryError('coverage resource envelope reached; checkpoint preserved')
