"""
熔断器(Circuit Breaker)

给每一个 provider(比如 real-a, mock-a, mock-b)维护一个"电路开关"状态：
- CLOSED    ：正常，请求可以打过去
- OPEN      ：最近失败太多次，暂时不再尝试，直接判定为"不可用"
- HALF_OPEN ：冷却时间到了，允许放行 1 个"试探请求"，看它是否恢复

为什么需要它：
如果一个 provider 已经挂了，还一直不停地把请求打过去，既浪费时间等它超时，
又可能让它更难恢复（雪上加霜）。不如"拉闸"——一段时间内直接跳过它，
过一会儿再小心地探一下它是不是好了。

用法示例：
    breaker = CircuitBreakerRegistry()

    if breaker.allow_request("real-a"):
        try:
            result = call_provider(...)
            breaker.record_success("real-a")
        except Exception:
            breaker.record_failure("real-a")
            raise
    else:
        # 直接跳过 real-a,router 会去选别的 provider
        ...
"""
import time
import threading
from enum import Enum


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _ProviderBreaker:
    """一个 provider 对应的熔断器状态机。"""

    def __init__(self, failure_threshold: int = 5, window_seconds: int = 60,
                 cooldown_seconds: int = 30):
        # failure_threshold：在 window_seconds 这段时间窗口内，失败几次就触发熔断
        # cooldown_seconds ：熔断后过多久，允许放一个试探请求进来
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds

        self.state = State.CLOSED
        self._failure_timestamps: list[float] = []
        self._opened_at: float | None = None
        self._half_open_probe_in_flight = False
        self._lock = threading.Lock()

    def _prune_old_failures(self, now: float):
        # 只统计时间窗口内的失败次数，太久以前的失败不该继续算数
        cutoff = now - self.window_seconds
        self._failure_timestamps = [t for t in self._failure_timestamps if t > cutoff]

    def allow_request(self) -> bool:
        """在真正发请求之前调用：问熔断器"这次请求能不能放行"。"""
        now = time.time()
        with self._lock:
            if self.state == State.CLOSED:
                return True

            if self.state == State.OPEN:
                # 冷却时间到了没？到了就进入半开状态，允许试探
                if self._opened_at is not None and (now - self._opened_at) >= self.cooldown_seconds:
                    self.state = State.HALF_OPEN
                    self._half_open_probe_in_flight = False
                else:
                    return False

            if self.state == State.HALF_OPEN:
                # 半开状态只允许一个试探请求在飞行中，避免一次性涌入太多请求
                if self._half_open_probe_in_flight:
                    return False
                self._half_open_probe_in_flight = True
                return True

        return False

    def record_success(self):
        """请求成功后调用：无论之前什么状态，成功就恢复到 CLOSED。"""
        with self._lock:
            self.state = State.CLOSED
            self._failure_timestamps.clear()
            self._opened_at = None
            self._half_open_probe_in_flight = False

    def record_failure(self):
        """请求失败后调用：更新失败计数，判断要不要跳到 OPEN。"""
        now = time.time()
        with self._lock:
            if self.state == State.HALF_OPEN:
                # 试探请求都失败了，说明还没恢复，重新回到 OPEN，重新计时冷却
                self.state = State.OPEN
                self._opened_at = now
                self._half_open_probe_in_flight = False
                return

            self._failure_timestamps.append(now)
            self._prune_old_failures(now)
            if len(self._failure_timestamps) >= self.failure_threshold:
                self.state = State.OPEN
                self._opened_at = now

    def record_cancelled(self):
        """Release a half-open probe abandoned because the client disconnected.

        A downstream cancellation says nothing about provider health. Closed
        breakers keep their existing history; a half-open breaker returns to
        OPEN so a future request can perform a fresh probe after cooldown.
        """
        now = time.time()
        with self._lock:
            if self.state == State.HALF_OPEN:
                self.state = State.OPEN
                self._opened_at = now
                self._half_open_probe_in_flight = False

    def reason(self) -> str:
        return {
            State.CLOSED: "healthy",
            State.OPEN: "circuit_open",
            State.HALF_OPEN: "half_open_probe",
        }[self.state]


class CircuitBreakerRegistry:
    """统一管理所有 provider 各自的熔断器,router.py 只需要跟这一个对象打交道。"""

    def __init__(self, failure_threshold: int = 5, window_seconds: int = 60,
                 cooldown_seconds: int = 30):
        self._breakers: dict[str, _ProviderBreaker] = {}
        self._defaults = dict(failure_threshold=failure_threshold,
                               window_seconds=window_seconds,
                               cooldown_seconds=cooldown_seconds)
        self._lock = threading.Lock()

    def _get(self, provider_name: str) -> _ProviderBreaker:
        with self._lock:
            if provider_name not in self._breakers:
                self._breakers[provider_name] = _ProviderBreaker(**self._defaults)
            return self._breakers[provider_name]

    def allow_request(self, provider_name: str) -> bool:
        return self._get(provider_name).allow_request()

    def record_success(self, provider_name: str):
        self._get(provider_name).record_success()

    def record_failure(self, provider_name: str):
        self._get(provider_name).record_failure()

    def record_cancelled(self, provider_name: str):
        self._get(provider_name).record_cancelled()

    def health_snapshot(self) -> dict[str, str]:
        """
        生成给 router.select_provider() 用的 health 字典。

        router.py 里的 _healthy() 只关心值是不是 "down",
        其他任何字符串都会被当成"健康"。这里把 OPEN 状态显式映射成 "down",
        这样 A 那边的代码完全不用改，你的熔断器状态就能被路由认出来。
        """
        snapshot = {}
        with self._lock:
            for name, breaker in self._breakers.items():
                snapshot[name] = "down" if breaker.state == State.OPEN else "healthy"
        return snapshot

    def debug_snapshot(self) -> dict[str, str]:
        """给 /provider-status 这类调试端点用，展示更细的原因（比如 half_open_probe)。"""
        with self._lock:
            return {name: b.reason() for name, b in self._breakers.items()}
