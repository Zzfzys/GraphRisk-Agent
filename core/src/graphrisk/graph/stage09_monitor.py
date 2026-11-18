"""RAM/disk continuous monitoring; bounded GPU probes at phase boundaries."""
import subprocess
import threading
from datetime import UTC, datetime
from graphrisk.demo.stage09_evaluation import resources, continuous_ok, write_json, Monitor

class DiagnosisMonitor(Monitor):
    def __init__(self, budget, path):
        super().__init__(budget, path)
        self.record_lock = threading.Lock()

    def record(self, observation):
        with self.record_lock:
            self.samples.append(observation)
            write_json(self.path, self.samples)

    def sample(self, gpu=False):
        for attempt in range(1, 4 if gpu else 2):
            try:
                observation = resources(gpu)
                if not continuous_ok(self.budget, observation):
                    self.violation.set()
                if gpu and observation["free_gpu_mib"] < self.budget.minimum_free_gpu_mib:
                    self.violation.set()
                if gpu:
                    observation["gpu_probe_attempt"] = attempt
                self.record(observation)
                return observation
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                retry = gpu and isinstance(error, subprocess.TimeoutExpired) and attempt < 3
                observation = {"at": datetime.now(UTC).isoformat(), "measurement_error": True,
                    "error_type": type(error).__name__, "gpu_probe": gpu, "attempt": attempt,
                    "bounded_retry_pending": retry}
                self.record(observation)
                if not retry:
                    self.violation.set()
                    return observation

    def loop(self):
        while not self.stop.wait(2):
            self.sample(False)

