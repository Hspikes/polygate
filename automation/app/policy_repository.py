"""
Policy 存储仓库：定义"策略文档存在哪、怎么读写"的抽象接口，以及一个
供测试/本地开发用的内存实现。

真正的 K8s ConfigMap 实现留给 Task 3；Task 2 只需要这个协议 + 内存版，
让 PolicyManager 的生命周期逻辑能先跑起来、能测。

并发安全靠 revision（版本戳）+ compare_and_swap：写入时必须带上"我读到的
revision"，如果期间别人已经改过（revision 变了），写入会被拒绝（抛
PolicyConflict），避免两个管理员同时改互相覆盖。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

from automation.app.policy_models import PolicyStoreDocument


class PolicyConflict(Exception):
    """compare_and_swap 时，期望的 revision 与当前不一致——说明期间被别人改过。"""


@dataclass(frozen=True)
class RepositorySnapshot:
    """一次读取的快照：文档内容 + 当时的 revision 戳。"""
    document: PolicyStoreDocument
    revision: str


class PolicyRepository(Protocol):
    """存储仓库协议。Task 3 会实现一个真正读写 K8s ConfigMap 的版本。"""

    def load(self) -> RepositorySnapshot:
        ...

    def compare_and_swap(
        self,
        document: PolicyStoreDocument,
        expected_revision: str,
    ) -> RepositorySnapshot:
        ...


class InMemoryPolicyRepository:
    """内存版仓库，带锁 + 单调递增的 revision，供测试和本地开发用。"""

    def __init__(self, document: PolicyStoreDocument) -> None:
        self._lock = threading.Lock()
        self._document = document
        self._revision_counter = 1
        self._revision = "rev-1"

    def load(self) -> RepositorySnapshot:
        with self._lock:
            return RepositorySnapshot(document=self._document, revision=self._revision)

    def compare_and_swap(
        self,
        document: PolicyStoreDocument,
        expected_revision: str,
    ) -> RepositorySnapshot:
        with self._lock:
            if expected_revision != self._revision:
                raise PolicyConflict(
                    f"expected revision {expected_revision!r} but current is "
                    f"{self._revision!r}"
                )
            self._revision_counter += 1
            self._revision = f"rev-{self._revision_counter}"
            self._document = document
            return RepositorySnapshot(document=self._document, revision=self._revision)