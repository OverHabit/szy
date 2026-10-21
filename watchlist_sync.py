"""跨端同步自选清单。

单一真源放在 GitHub 仓库的 `watchlist-state` 分支上的 `watchlist.json`：
本机脚本和 Streamlit 云端页面都读写这同一个文件，各自保留一份 SQLite 作为离线缓存，
所以行情抓取与收盘信号永远不依赖网络是否可用。

设计取舍
- 用独立分支而不是 master：往 master 写入会触发 Streamlit Cloud 重新部署，每次改自选都会
  重启应用；独立分支不会被部署监听。
- **网页端为准**：远端 revision 更新时，本机无条件让位（包括本机尚未推送的改动）；
  本机只有在"基于最新远端"时才允许推送，避免把网页端的修改盖掉。
- 令牌只从环境变量 / streamlit secrets 读取，不写入仓库。
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from watchlist_store import (
    export_watchlist,
    get_setting,
    replace_watchlist,
    set_setting,
)

REPO = "OverHabit/szy"
BRANCH = "watchlist-state"
PATH = "watchlist.json"
API = "https://api.github.com"
TIMEOUT = 15
TOKEN_ENV = "WATCHLIST_SYNC_TOKEN"
TOKEN_FILE = Path(__file__).resolve().parent / ".github_token"


class SyncError(RuntimeError):
    """同步失败；调用方应当降级为使用本机缓存，而不是中断流程。"""


# --------------------------------------------------------------------------- 配置


def _secret(name: str) -> str | None:
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    stored = None
    try:  # Streamlit 云端把令牌放在 secrets 里
        import streamlit as st

        try:
            stored = st.secrets[name]
        except Exception:
            stored = st.secrets.get(name)
    except Exception:
        return None
    return str(stored).strip() if stored else None


def _token() -> str | None:
    token = _secret(TOKEN_ENV)
    if token:
        return token
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    return token or None


def config() -> dict[str, str]:
    token = _token()
    if not token:
        raise SyncError(f"未配置同步令牌：请设置环境变量 {TOKEN_ENV} 或提供 .github_token")
    return {
        "token": token,
        "repo": _secret("WATCHLIST_SYNC_REPO") or REPO,
        "branch": _secret("WATCHLIST_SYNC_BRANCH") or BRANCH,
        "path": _secret("WATCHLIST_SYNC_PATH") or PATH,
    }


def sync_available() -> bool:
    """没有令牌、或显式关闭时，整个同步层静默停用，行为与改造前完全一致。"""
    if os.environ.get("WATCHLIST_SYNC_DISABLED", "").strip() in {"1", "true", "yes"}:
        return False
    try:
        config()
    except SyncError:
        return False
    return True


# --------------------------------------------------------------------------- HTTP


def _request(cfg: dict[str, str], method: str, url: str, payload: dict | None = None) -> tuple[int, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {cfg['token']}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "quant-watchlist-sync/1.0",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:200]
        except Exception:
            pass
        return exc.code, detail
    except Exception as exc:  # 网络不可用、超时
        raise SyncError(f"无法访问 GitHub：{type(exc).__name__}") from exc


def _contents_url(cfg: dict[str, str]) -> str:
    return f"{API}/repos/{cfg['repo']}/contents/{cfg['path']}"


def fetch_state(cfg: dict[str, str] | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """读取远端状态，返回 (state, sha)；文件尚不存在时返回 (None, None)。"""
    cfg = cfg or config()
    code, body = _request(cfg, "GET", f"{_contents_url(cfg)}?ref={cfg['branch']}")
    if code == 404:
        return None, None
    if code != 200:
        raise SyncError(f"读取远端清单失败（HTTP {code}）：{body}")
    try:
        content = base64.b64decode(body["content"]).decode("utf-8")
        return json.loads(content), body["sha"]
    except Exception as exc:
        raise SyncError("远端清单内容无法解析") from exc


def _write_state(cfg: dict[str, str], state: dict[str, Any], sha: str | None, message: str) -> None:
    payload = {
        "message": message,
        "content": base64.b64encode(json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii"),
        "branch": cfg["branch"],
    }
    if sha:
        payload["sha"] = sha
    code, body = _request(cfg, "PUT", _contents_url(cfg), payload)
    if code not in (200, 201):
        raise SyncError(f"写入远端清单失败（HTTP {code}）：{body}")


# --------------------------------------------------------------------------- 同步


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _origin() -> str:
    """标注这次写入来自哪一端，方便在 GitHub 提交历史里追溯。"""
    try:
        from streamlit.runtime import exists

        return "web" if exists() else "local"
    except Exception:
        return "local"


def push(
    database_path: Path | str | None = None,
    cfg: dict[str, str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """把本机清单整表推上远端，revision +1。

    冲突规则：**网页端为准**。只要远端比本机新，本机推送直接作废，改为拉取远端版本；
    force=True 只用于初始化或人工修复。
    """
    cfg = cfg or config()
    for attempt in (1, 2):
        state, sha = fetch_state(cfg)
        remote_revision = int((state or {}).get("revision") or 0)
        local_revision = int(get_setting("sync_revision", "0", database_path) or 0)
        if state is not None and remote_revision > local_revision and not force:
            adopted = _adopt(state, database_path)
            return {"status": "superseded", **adopted}

        revision = remote_revision + 1
        new_state = {
            "version": 1,
            "revision": revision,
            "updated_at": _now(),
            "updated_by": _origin(),
            "entries": export_watchlist(database_path),
        }
        try:
            _write_state(cfg, new_state, sha, f"watchlist rev {revision}")
        except SyncError:
            if attempt == 2:
                raise
            continue  # sha 过期（另一端刚写过），重取一次
        set_setting("sync_revision", str(revision), database_path)
        set_setting("sync_updated_at", new_state["updated_at"], database_path)
        set_setting("sync_pending", "0", database_path)
        return {"status": "pushed", "revision": revision, "updated_at": new_state["updated_at"], "count": len(new_state["entries"])}
    raise SyncError("写入远端清单失败")


def _adopt(state: dict[str, Any], database_path: Path | str | None) -> dict[str, Any]:
    """用远端整表覆盖本机，并记录已同步到的 revision。"""
    revision = int(state.get("revision") or 0)
    kept = replace_watchlist(state.get("entries") or [], database_path)
    set_setting("sync_revision", str(revision), database_path)
    set_setting("sync_updated_at", str(state.get("updated_at") or ""), database_path)
    set_setting("sync_pending", "0", database_path)
    return {"revision": revision, "updated_at": state.get("updated_at"), "count": kept}


def pull(
    database_path: Path | str | None = None,
    cfg: dict[str, str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """把远端清单拉到本机。远端更新时无条件覆盖本机，包括尚未推送的本机改动。"""
    cfg = cfg or config()
    state, _ = fetch_state(cfg)
    local_revision = int(get_setting("sync_revision", "0", database_path) or 0)
    pending = get_setting("sync_pending", "0", database_path) == "1"

    if state is None:
        if local_revision == 0:
            return push(database_path, cfg, force=True)  # 远端还没有文件，用本机内容初始化
        return {"status": "up_to_date", "revision": local_revision}

    remote_revision = int(state.get("revision") or 0)
    if remote_revision > local_revision or force:
        adopted = _adopt(state, database_path)
        return {"status": "pulled", **adopted, "discarded_local": pending}
    if pending:
        # 只有本机基于最新远端时才允许推送，避免把网页端的改动盖掉
        return push(database_path, cfg)
    return {"status": "up_to_date", "revision": local_revision}


def ensure_synced(database_path: Path | str | None = None) -> dict[str, Any]:
    """尽最大努力同步一次，任何异常都降级为 error，绝不抛给调用方。"""
    if not sync_available():
        return {"status": "disabled", "message": "未配置同步令牌，仅使用本机清单"}
    try:
        return pull(database_path)
    except SyncError as exc:
        return {"status": "error", "message": str(exc)}
    except Exception as exc:  # 兜底，避免同步层把收盘信号任务带崩
        return {"status": "error", "message": f"{type(exc).__name__}: {exc}"}


def mark_pending(database_path: Path | str | None = None) -> None:
    """本机改动了清单：标记为待推送，下一次同步时优先推送。"""
    set_setting("sync_pending", "1", database_path)


def status(database_path: Path | str | None = None) -> dict[str, Any]:
    revision = get_setting("sync_revision", "0", database_path) or "0"
    return {
        "enabled": sync_available(),
        "revision": int(revision),
        "updated_at": get_setting("sync_updated_at", "", database_path) or "",
        "pending": get_setting("sync_pending", "0", database_path) == "1",
        "repo": _secret("WATCHLIST_SYNC_REPO") or REPO,
        "branch": _secret("WATCHLIST_SYNC_BRANCH") or BRANCH,
    }


def describe(result: dict[str, Any]) -> str:
    """把同步结果翻成一行中文，供网页和日志直接显示。"""
    kind = result.get("status")
    if kind == "pulled":
        suffix = "（已放弃本机未推送的改动）" if result.get("discarded_local") else ""
        return f"已从云端拉取最新清单（rev {result.get('revision')}，{result.get('count')} 项）{suffix}"
    if kind == "pushed":
        return f"已把本机清单推送到云端（rev {result.get('revision')}，{result.get('count')} 项）"
    if kind == "superseded":
        return f"云端已有更新（rev {result.get('revision')}），已按网页端版本覆盖本机（{result.get('count')} 项）"
    if kind == "up_to_date":
        return f"云端与本机一致（rev {result.get('revision')}）"
    if kind == "disabled":
        return "同步未启用：未配置令牌，仅使用本机清单"
    return f"同步失败，已回退到本机清单：{result.get('message', '未知原因')}"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="自选清单跨端同步")
    parser.add_argument("action", choices=["status", "pull", "push"])
    args = parser.parse_args()

    if args.action == "status":
        info = status()
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    result = pull() if args.action == "pull" else push()
    print(describe(result))
    return 0 if result.get("status") in {"pulled", "pushed", "up_to_date"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
