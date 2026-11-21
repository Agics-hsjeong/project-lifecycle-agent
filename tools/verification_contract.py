"""Bind verification to immutable candidate bytes and its declared execution plan."""
import hashlib
import json
from types import MappingProxyType


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


TASK_FIELDS = ('id', 'title', 'role', 'inputs', 'requirement_ids', 'depends_on', 'stage')


def task_context(task):
    return {name: task.get(name) for name in TASK_FIELDS}


class VerificationContract:
    def __init__(self, files, execution, task=None):
        # Byte strings and a private mapping survive filesystem swaps and test writes.
        self.files = MappingProxyType({name: bytes(value) for name, value in files.items()})
        self.plan_sha256 = digest(canonical(execution))
        self.task_sha256 = digest(canonical(task_context(task))) if task is not None else None
        self.manifest = {name: {"sha256": digest(value), "bytes": len(value)} for name, value in sorted(self.files.items())}
        self.candidate_sha256 = digest(canonical(self.manifest))
        self.manifest_sha256 = digest(canonical({"schema_version": 1, "files": self.manifest,
                                               "execution_sha256": self.plan_sha256, "task_context_sha256": self.task_sha256}))

    def assert_unchanged(self, current, execution):
        if digest(canonical(execution)) != self.plan_sha256:
            raise ValueError("검증 중 실행 계획이 변경되어 결과를 반영하지 않습니다.")
        if set(current) != set(self.files) or any(current[name] != value for name, value in self.files.items()):
            raise ValueError("검증 대상 파일이 변경되어 결과를 반영하지 않습니다. 생성·변환 후 다시 검증하세요.")

    def receipt(self, published):
        verified = {name: {"sha256": digest(value), "bytes": len(value)} for name, value in sorted(published.items())}
        return {"schema_version": 1, "candidate_sha256": self.candidate_sha256,
                "verification_manifest_sha256": self.manifest_sha256, "execution_sha256": self.plan_sha256,
                "task_context_sha256": self.task_sha256,
                "verified_outputs": verified, "verified_output_sha256": digest(canonical(verified)),
                "published_output_sha256": digest(canonical(verified))}
