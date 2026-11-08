"""Stable identity for a portfolio snapshot."""
import hashlib
import json


def snapshot_context(data: dict) -> str:
    snapshot = {key: value for key, value in data.items() if key not in {"settings", "source"}}
    snapshot["free_cash"] = float(snapshot["free_cash"])
    snapshot["contract"] = {**snapshot["contract"],
                            "point_value": float(snapshot["contract"]["point_value"]),
                            "margin": float(snapshot["contract"]["margin"])}
    snapshot["history"] = [{key: value if key == "date" else float(value)
                            for key, value in row.items()} for row in snapshot["history"]]
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
