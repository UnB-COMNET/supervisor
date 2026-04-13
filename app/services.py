""" Supervisor monitoring service """

import collections
import logging
import threading
import time

import requests

from app import cdn_qoe

logger = logging.getLogger(__name__)

MONITOR_INTERVAL = 5 # seconds
DELAY_THRESHOLD_MS = 100.0 # midpoint between normal (~40ms) and degraded (~125ms)
THROUGHPUT_THRESHOLD_BPS = 125e6 # 125 Mbit/s
THROUGHPUT_WINDOW = 5 # number of samples for the moving average


class SupervisorService:
    def __init__(self, onos_base_url: str, deployer_base_url: str):
        self.onos_base_url = onos_base_url
        self.deployer_base_url = deployer_base_url

        self._last_bytes = None
        self._last_bytes_time = None
        self._throughput_samples = collections.deque(maxlen=THROUGHPUT_WINDOW)
        self._path = None
        self._access_delay_ms = 0.0

        self._timer = None
        self._lock = threading.Lock()

    # Brief: Stores the current path sent by the deployer, then starts the monitor loop.
    # Resets throughput state so stale samples from the previous session don't pollute the new window.
    def update(self, path: list, access_delay_ms: float = 0.0):
        with self._lock:
            self._path = path
            self._access_delay_ms = access_delay_ms
            self._throughput_samples.clear()
            self._last_bytes = None
            self._last_bytes_time = None

        logger.info("New path received: %s  (access_delay=%.1f ms)", path, access_delay_ms)
        self._restart_monitor()

    # Brief: Cancels any running timer and schedules a fresh monitor cycle
    def _restart_monitor(self):
        if self._timer is not None:
            self._timer.cancel()
        self._schedule_next()

    # Brief: Schedules the next monitor cycle after MONITOR_INTERVAL seconds
    def _schedule_next(self):
        self._timer = threading.Timer(MONITOR_INTERVAL, self._monitor_cycle)
        self._timer.daemon = True
        self._timer.start()

    # Brief: Every MONITOR_INTERVAL seconds: measures delay and throughput (moving avg)
    # Triggers recalculate if delay > DELAY_THRESHOLD_MS or throughput avg < THROUGHPUT_THRESHOLD_BPS
    def _monitor_cycle(self):
        with self._lock:
            if self._path is None:
                return
            current_path = list(self._path)
            access_delay_ms = self._access_delay_ms

        try:
            delay_ms = self._measure_path_delay(current_path, access_delay_ms)
            throughput_avg_bps = self._measure_path_throughput()

            logger.info(
                "delay=%.1f ms | throughput_avg=%.2f Mbit/s (window=%d)",
                delay_ms, throughput_avg_bps / 1e6, len(self._throughput_samples),
            )

            if delay_ms > DELAY_THRESHOLD_MS:
                logger.warning(
                    "*** Delay %.1f ms > %.0f ms threshold -> recalculate ***",
                    delay_ms, DELAY_THRESHOLD_MS,
                )
                self._notify_deployer_recalculate()
                return # deployer will call /supervise again with the new path

            if (len(self._throughput_samples) == THROUGHPUT_WINDOW
                    and throughput_avg_bps < THROUGHPUT_THRESHOLD_BPS):
                logger.warning(
                    "*** Throughput avg %.2f Mbit/s < %.0f Mbit/s threshold -> recalculate ***",
                    throughput_avg_bps / 1e6, THROUGHPUT_THRESHOLD_BPS / 1e6,
                )
                self._notify_deployer_recalculate()
                return # deployer will call /supervise again with the new path

            self._schedule_next()

        except Exception as e:
            logger.error("Monitor cycle error: %s", e)
            self._schedule_next()

    # Brief: Refreshes RTT_MATRIX via ONOS CLI and computes the end-to-end path delay:
    #   - Sums RTT_MATRIX[i][j] for every inter-switch edge in the path
    #   - Adds 2 * access_delay_ms to account for the client-side and server-side access links
    def _measure_path_delay(self, path: list, access_delay_ms: float) -> float:
        cdn_qoe.get_dynamic_latencies()
        core_ms = 0.0
        for edge in path:
            i, j = int(edge[0]), int(edge[1])
            link_delay = cdn_qoe.RTT_MATRIX[i][j]
            logger.debug("[delay] Edge %s -> %s  delay=%.1f ms", cdn_qoe.ESTADOS[i], cdn_qoe.ESTADOS[j], link_delay)
            core_ms += link_delay

        total_ms = core_ms + 2 * access_delay_ms
        logger.debug("[delay] Core=%.1f ms  Access=2x%.1f ms  Total=%.1f ms", core_ms, access_delay_ms, total_ms)
        return total_ms

    # Brief: Queries ONOS REST API port stats on ES (of:0000000000000001), port 3 (ES->ds0).
    #   - Port 3 is the server-facing port: bytesSent here equals all data delivered to ds0
    #     regardless of which upstream path (MG or RJ) was used.
    #   - Computes instantaneous throughput, appends to a sliding window, returns the window average.
    def _measure_path_throughput(self) -> float:
        device_id = cdn_qoe.DEVICE_MAP["ES"]
        url  = f"{self.onos_base_url}/statistics/ports/{device_id}"
        auth = ("onos", "rocks")

        try:
            ports = requests.get(url, auth=auth, timeout=5).json()["statistics"][0]["ports"]
            port = next((p for p in ports if p["port"] == 3), None)

            if port is None:
                logger.warning("Port 3 not found on ES statistics")
                return 0.0

            b2 = port["bytesSent"]
            t2 = time.time()

            if self._last_bytes is None:
                self._last_bytes = b2
                self._last_bytes_time = t2
                return 0.0

            if b2 == self._last_bytes:
                bps = 0.0
            else:
                bps = (b2 - self._last_bytes) * 8 / (t2 - self._last_bytes_time)

            self._last_bytes = b2
            self._last_bytes_time = t2

            self._throughput_samples.append(bps)
            return sum(self._throughput_samples) / len(self._throughput_samples)

        except Exception as e:
            logger.error("Throughput measure error: %s", e)
            return 0.0

    # Brief: POSTs to /deploy/recalculate on the deployer, signalling that the path should be recomputed.
    # Timeout is 30s to account for the deployer's flow installation wait
    def _notify_deployer_recalculate(self):
        resp = requests.post(
            self.deployer_base_url + "/recalculate",
            timeout=30,
        )
        resp.raise_for_status()
        time.sleep(2)
        logger.info("Deployer notified - recalculate requested.")
