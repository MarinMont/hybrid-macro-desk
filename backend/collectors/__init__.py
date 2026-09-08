"""
collectors — /api/market /api/derivs /api/macro /api/ai-overview /api/entry-state /api/agg-delta /api/setup/*

各モジュールは以下を公開する疎結合コンポーネント:
  - router : FastAPI APIRouter (エンドポイント)
  - init(http, bucket, store) : 共有依存 (httpxクライアント / HLレートバケット / Store) を注入
  - run()  : ポーリングループを起動する coroutine (startup で create_task)

liqmap_service.py が末尾で各モジュールを include_router + init + run する。
全ての外部呼び出しは timeout + try/except を持ち、失敗はパネル単位のフォールバックに留める
(画面全体を殺さない — SPEC §9)。
"""

from . import market, derivs, macro, ai, entry_state, aggdelta, setup_console  # noqa: F401

ALL = (market, derivs, macro, ai, entry_state, aggdelta, setup_console)
