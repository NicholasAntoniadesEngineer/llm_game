"""Performance monitoring utilities for Eternal Cities.

Provides comprehensive performance tracking, metrics collection,
and optimization insights for the building generation system.
"""

import time
import psutil
import threading
from typing import Dict, Any, List, Optional, Callable
from collections import defaultdict, deque
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)


class PerformanceMonitor:
    """Comprehensive performance monitoring system."""

    def __init__(self, max_samples: int = 1000):
        self.max_samples = max_samples
        self.metrics = defaultdict(lambda: deque(maxlen=max_samples))
        self.timers = {}
        self.counters = defaultdict(int)
        self.gauges = {}
        self.histograms = defaultdict(list)
        self._lock = threading.Lock()

        # System metrics
        self.system_start_time = time.time()
        self.process = psutil.Process()

    @contextmanager
    def timer(self, name: str, tags: Optional[Dict[str, Any]] = None):
        """Context manager for timing operations."""
        start_time = time.time()
        try:
            yield
        finally:
            duration = time.time() - start_time
            self.record_timing(name, duration, tags)

    def record_timing(self, name: str, duration: float, tags: Optional[Dict[str, Any]] = None):
        """Record a timing measurement."""
        with self._lock:
            self.metrics[f"timing.{name}"].append({
                'value': duration,
                'timestamp': time.time(),
                'tags': tags or {}
            })

    def increment_counter(self, name: str, value: int = 1, tags: Optional[Dict[str, Any]] = None):
        """Increment a counter."""
        with self._lock:
            self.counters[name] += value
            self.metrics[f"counter.{name}"].append({
                'value': self.counters[name],
                'timestamp': time.time(),
                'tags': tags or {}
            })

    def set_gauge(self, name: str, value: float, tags: Optional[Dict[str, Any]] = None):
        """Set a gauge value."""
        with self._lock:
            self.gauges[name] = value
            self.metrics[f"gauge.{name}"].append({
                'value': value,
                'timestamp': time.time(),
                'tags': tags or {}
            })

    def record_histogram(self, name: str, value: float, tags: Optional[Dict[str, Any]] = None):
        """Record a histogram value."""
        with self._lock:
            self.histograms[name].append({
                'value': value,
                'timestamp': time.time(),
                'tags': tags or {}
            })

    def collect_system_metrics(self):
        """Collect current system performance metrics."""
        try:
            # CPU usage
            self.set_gauge('system.cpu_percent', self.process.cpu_percent())

            # Memory usage
            memory_info = self.process.memory_info()
            self.set_gauge('system.memory_rss', memory_info.rss)
            self.set_gauge('system.memory_vms', memory_info.vms)

            # Memory percentage
            memory_percent = self.process.memory_percent()
            self.set_gauge('system.memory_percent', memory_percent)

            # Thread count
            self.set_gauge('system.threads', self.process.num_threads())

            # Open file descriptors (Unix only)
            try:
                self.set_gauge('system.open_files', len(self.process.open_files()))
            except (psutil.AccessDenied, AttributeError):
                pass

        except Exception as e:
            logger.warning(f"Failed to collect system metrics: {e}")

    def get_summary_stats(self, name: str) -> Dict[str, Any]:
        """Get summary statistics for a metric."""
        with self._lock:
            if name not in self.metrics:
                return {}

            values = [m['value'] for m in self.metrics[name] if isinstance(m, dict)]

            if not values:
                return {}

            return {
                'count': len(values),
                'min': min(values),
                'max': max(values),
                'avg': sum(values) / len(values),
                'latest': values[-1] if values else None
            }

    def get_all_metrics_summary(self) -> Dict[str, Any]:
        """Get summary of all metrics."""
        summary = {}

        with self._lock:
            # Timing metrics
            for key in list(self.metrics.keys()):
                if key.startswith('timing.'):
                    summary[key] = self.get_summary_stats(key)

            # Counter metrics
            for key, value in self.counters.items():
                summary[f"counter.{key}"] = {'total': value}

            # Gauge metrics
            for key, value in self.gauges.items():
                summary[f"gauge.{key}"] = {'current': value}

            # Histogram summaries
            for key in self.histograms:
                values = [h['value'] for h in self.histograms[key]]
                if values:
                    summary[f"histogram.{key}"] = {
                        'count': len(values),
                        'min': min(values),
                        'max': max(values),
                        'avg': sum(values) / len(values)
                    }

        return summary

    def get_performance_report(self) -> str:
        """Generate a human-readable performance report."""
        summary = self.get_all_metrics_summary()

        report_lines = ["Performance Report", "=" * 50]

        # System uptime
        uptime = time.time() - self.system_start_time
        report_lines.append(f"System Uptime: {uptime:.1f} seconds")

        # Timing metrics
        timing_metrics = {k: v for k, v in summary.items() if k.startswith('timing.')}
        if timing_metrics:
            report_lines.append("\nTiming Metrics:")
            for name, stats in timing_metrics.items():
                metric_name = name.replace('timing.', '')
                if stats:
                    report_lines.append(f"  {metric_name}:")
                    report_lines.append(f"    Count: {stats.get('count', 0)}")
                    report_lines.append(f"    Avg: {stats.get('avg', 0):.3f}s")
                    report_lines.append(f"    Min: {stats.get('min', 0):.3f}s")
                    report_lines.append(f"    Max: {stats.get('max', 0):.3f}s")

        # Counter metrics
        counter_metrics = {k: v for k, v in summary.items() if k.startswith('counter.')}
        if counter_metrics:
            report_lines.append("\nCounter Metrics:")
            for name, stats in counter_metrics.items():
                metric_name = name.replace('counter.', '')
                report_lines.append(f"  {metric_name}: {stats.get('total', 0)}")

        # Gauge metrics
        gauge_metrics = {k: v for k, v in summary.items() if k.startswith('gauge.')}
        if gauge_metrics:
            report_lines.append("\nGauge Metrics:")
            for name, stats in gauge_metrics.items():
                metric_name = name.replace('gauge.', '')
                report_lines.append(f"  {metric_name}: {stats.get('current', 0)}")

        return "\n".join(report_lines)

    def reset(self):
        """Reset all metrics."""
        with self._lock:
            self.metrics.clear()
            self.timers.clear()
            self.counters.clear()
            self.gauges.clear()
            self.histograms.clear()


# Global performance monitor instance
performance_monitor = PerformanceMonitor()


def monitor_function(func: Callable) -> Callable:
    """Decorator to monitor function performance."""
    def wrapper(*args, **kwargs):
        with performance_monitor.timer(f"function.{func.__name__}"):
            return func(*args, **kwargs)
    return wrapper


def monitor_agent_call(agent_name: str, operation: str):
    """Context manager for monitoring agent operations."""
    return performance_monitor.timer(f"agent.{agent_name}.{operation}")


def record_building_generation(building_type: str, duration: float, success: bool):
    """Record building generation metrics."""
    performance_monitor.record_timing(
        f"building.{building_type}",
        duration,
        {'success': success}
    )

    if success:
        performance_monitor.increment_counter(f"building.{building_type}.success")
    else:
        performance_monitor.increment_counter(f"building.{building_type}.failure")


def record_city_generation(district_count: int, building_count: int, duration: float):
    """Record city generation metrics."""
    performance_monitor.record_timing("city.generation", duration, {
        'districts': district_count,
        'buildings': building_count
    })

    performance_monitor.set_gauge("city.districts", district_count)
    performance_monitor.set_gauge("city.buildings", building_count)


def get_performance_snapshot() -> Dict[str, Any]:
    """Get a snapshot of current performance metrics."""
    performance_monitor.collect_system_metrics()
    return {
        'timestamp': time.time(),
        'system': {
            'uptime': time.time() - performance_monitor.system_start_time,
            'cpu_percent': performance_monitor.gauges.get('system.cpu_percent', 0),
            'memory_percent': performance_monitor.gauges.get('system.memory_percent', 0),
            'threads': performance_monitor.gauges.get('system.threads', 0)
        },
        'application': performance_monitor.get_all_metrics_summary()
    }


# Convenience functions for common monitoring tasks
def start_operation(operation_name: str):
    """Start timing an operation."""
    return performance_monitor.timer(operation_name)


def end_operation(operation_name: str, success: bool = True):
    """End timing an operation (use with manual timing)."""
    # This is a simplified version - for full functionality,
    # use the context manager approach
    pass


def log_performance_warning(operation: str, duration: float, threshold: float = 5.0):
    """Log a performance warning if duration exceeds threshold."""
    if duration > threshold:
        logger.warning(f"Performance warning: {operation} took {duration:.2f}s (threshold: {threshold}s)")


def log_performance_error(operation: str, duration: float, threshold: float = 30.0):
    """Log a performance error if duration exceeds threshold."""
    if duration > threshold:
        logger.error(f"Performance error: {operation} took {duration:.2f}s (threshold: {threshold}s)")