"""sleep-data.py（マイ・ライフ OS v5.4 対応版）

Garmin Connect から取得した睡眠・HRV・Body Battery・歩数を
Notion の「Garmin日次」DB に書き込み、同時に「日次ジャーナル」DB の
同日レコードを検索（無ければ自動作成）して Relation で紐付ける。

元の OSS（chloevoyer/garmin-to-notion）からの主な変更点：
1. プロパティ名を v5.4 仕様の英語に変更（Sleep Score / Total Sleep / etc.）
2. Daily Journal の同日エントリを検索し、Relation「日次ジャーナル」を自動セット
3. Title プロパティを明示的にセット（"Garmin YYYY-MM-DD"）
4. 環境変数：NOTION_DAILY_DB_ID を追加（日次ジャーナル DB の ID）
5. Body Battery 開始/終了 を「最大/最小」に厳密化（OSS 元コードは先頭/末尾値）

このファイルは https://github.com/qqzf5bpd-cell/garmin-to-notion/ の
sleep-data.py を置き換えて使う想定。
"""

from datetime import date, timedelta
import os
import json
import base64

import garth
from garth.auth_tokens import OAuth2Token
from garminconnect import Garmin
from notion_client import Client
from dotenv import load_dotenv

load_dotenv()


# ──────────────────────────────────────────────────────
# プロパティ名定数（マイ・ライフ OS v5.4 仕様）
# ──────────────────────────────────────────────────────

# Garmin日次 DB のプロパティ名（英語）
GP_TITLE       = "Title"
GP_DATE        = "Date"
GP_SLEEP_SCORE = "Sleep Score"
GP_TOTAL_SLEEP = "Total Sleep"
GP_DEEP_SLEEP  = "Deep Sleep"
GP_LIGHT_SLEEP = "Light Sleep"
GP_REM_SLEEP   = "REM Sleep"
GP_BB_HIGHEST  = "Body Battery Highest"
GP_BB_LOWEST   = "Body Battery Lowest"
GP_RHR         = "Resting Heart Rate"
GP_HRV         = "HRV"
GP_STEPS       = "Steps"
GP_DAILY_REL   = "日次ジャーナル"  # Phase 2 で設定した synced inverse 名

# 日次ジャーナル DB のプロパティ名（日本語）
DP_TITLE = "タイトル"
DP_DATE  = "日付"


# ──────────────────────────────────────────────────────
# Garmin 認証
# ──────────────────────────────────────────────────────

def _try_load_tokens(raw: str) -> bool:
    """様々な形式の GARMIN_TOKENS を試して garth.client にロードする。

    試す順序：
    1. garth.client.loads(raw)：canonical 形式（[oauth1, oauth2] dict 2 要素 list）
    2. dict 形式 {"oauth2_token": {...}}：旧 garth
    3. list 形式で [1] が JSON 文字列のパターン：1 文字列を dict にパースしてから
    """
    last_err: Exception | None = None

    # 試行 1：garth 純正
    try:
        garth.client.loads(raw)
        if garth.client.oauth2_token is not None:
            return True
    except Exception as e:
        last_err = e

    # 試行 2 以降：JSON 解析して手動マッピング
    try:
        token_data = json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"GARMIN_TOKENS が JSON として解析できません: {e}") from e

    # 試行 2：dict {"oauth2_token": {...}}
    if isinstance(token_data, dict) and "oauth2_token" in token_data:
        oauth2_data = token_data["oauth2_token"]
        if isinstance(oauth2_data, str):
            oauth2_data = json.loads(oauth2_data)
        return _set_oauth2_from_dict(oauth2_data)

    # 試行 3：dict が直に oauth2
    if isinstance(token_data, dict) and "access_token" in token_data:
        return _set_oauth2_from_dict(token_data)

    # 試行 4：list 形式の各種
    if isinstance(token_data, list):
        for i, item in enumerate(token_data):
            inner = item
            # 文字列なら JSON パース試行
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner)
                except Exception:
                    continue
            if isinstance(inner, dict) and "access_token" in inner:
                return _set_oauth2_from_dict(inner)
        # ここまでで見つからない場合は詳細エラー
        types = [type(x).__name__ for x in token_data]
        raise RuntimeError(
            f"GARMIN_TOKENS list 内に oauth2 dict が見つかりません。要素型：{types}。"
            f"（最初の試行 garth.client.loads() のエラー：{last_err}）"
        )

    raise RuntimeError(f"GARMIN_TOKENS の構造が不明: type={type(token_data).__name__}")


def _set_oauth2_from_dict(oauth2_data: dict) -> bool:
    """OAuth2Token のコンストラクタ既知フィールドのみフィルタしてセット。"""
    import inspect
    sig = inspect.signature(OAuth2Token)
    allowed = set(sig.parameters.keys())
    filtered = {k: v for k, v in oauth2_data.items() if k in allowed}
    garth.client.oauth2_token = OAuth2Token(**filtered)
    return True


def get_garmin_client() -> Garmin:
    """OAuth2 トークンを使って Garmin クライアントを生成する。

    GitHub Actions では IP が Garmin/Cloudflare でブロックされ、パスワード/MFA
    認証は不可能（CAPTCHA 要求や 429 が返る）。よって GARMIN_TOKENS は必須。

    garminconnect v0.3+ は deprecated garth ではなく独自 client を使うため、
    g.garth.loads() を最優先で試し、失敗時は旧 garth.client にフォールバックする。
    """
    tokens = os.getenv("GARMIN_TOKENS")
    if not tokens or not tokens.strip():
        raise RuntimeError(
            "GARMIN_TOKENS が未設定または空白文字のみです。GitHub Actions では"
            "パスワード認証は使えないため、ローカル PC で "
            "`python garmin/generate_garmin_tokens.py` を実行してトークンを生成し、"
            "出力された base64 文字列を GitHub Secrets の GARMIN_TOKENS に設定してください。"
        )

    # 改行や前後空白を除去
    tokens = tokens.strip()
    print(f"  GARMIN_TOKENS 読み込み：{len(tokens)} chars（先頭：{tokens[:8]}…）")

    try:
        raw = base64.b64decode(tokens).decode()
    except Exception:
        raw = tokens
    raw = raw.strip()
    if not raw:
        raise RuntimeError("GARMIN_TOKENS をデコードした結果が空。Secret を再確認してください。")
    print(f"  デコード後：{len(raw)} chars（先頭：{raw[:30]}…）")

    g = Garmin()
    last_err: Exception | None = None

    # 戦略 1：g.garth.loads()（garminconnect v0.3+ 内蔵 client）
    if hasattr(g, "garth") and g.garth is not None and hasattr(g.garth, "loads"):
        try:
            g.garth.loads(raw)
            if getattr(g.garth, "oauth2_token", None) is not None:
                print("✅ 保存済みトークンでログイン成功（g.garth.loads）")
                return g
        except Exception as e:
            last_err = e
            print(f"  ✗ g.garth.loads() 失敗：{e}")

    # 戦略 2：旧 garth.client.loads()（deprecated だが念のため）
    try:
        garth.client.loads(raw)
        if garth.client.oauth2_token is not None:
            g.garth = garth.client
            print("✅ 保存済みトークンでログイン成功（garth.client.loads・legacy）")
            return g
    except Exception as e:
        last_err = last_err or e
        print(f"  ✗ garth.client.loads() 失敗：{e}")

    # 戦略 3：手動 JSON パース → OAuth2Token 構築
    try:
        _try_load_tokens(raw)
        g.garth = garth.client
        print("✅ 保存済みトークンでログイン成功（手動構築・legacy）")
        return g
    except Exception as e:
        raise RuntimeError(
            f"GARMIN_TOKENS のロードに全方法で失敗：{e}\n"
            f"  最初のエラー：{last_err}\n"
            "対処：ローカル PC で `python garmin/generate_garmin_tokens.py` を実行し、"
            "出力された base64 文字列で GitHub Secrets の GARMIN_TOKENS を更新してください。"
        ) from e


# ──────────────────────────────────────────────────────
# Notion 補助
# ──────────────────────────────────────────────────────

def find_page_by_date(client: Client, db_id: str, prop_name: str, target_date: str) -> str | None:
    """指定 DB から prop_name == target_date のページ ID を返す（最初の一致）。"""
    res = client.databases.query(
        database_id=db_id,
        filter={"property": prop_name, "date": {"equals": target_date}},
        page_size=1,
    )
    results = res.get("results", [])
    return results[0]["id"] if results else None


def ensure_daily_page(client: Client, daily_db_id: str, target_date: str) -> str:
    """日次ジャーナルの同日エントリ。なければ最小スタブを作成。"""
    pid = find_page_by_date(client, daily_db_id, DP_DATE, target_date)
    if pid:
        return pid
    print(f"  日次ジャーナルにエントリなし → スタブ作成 ({target_date})")
    response = client.pages.create(
        parent={"database_id": daily_db_id},
        properties={
            DP_TITLE: {"title": [{"text": {"content": target_date}}]},
            DP_DATE:  {"date":  {"start": target_date}},
        },
        icon={"emoji": "📔"},
    )
    return response["id"]


def ensure_garmin_page(
    client: Client,
    garmin_db_id: str,
    target_date: str,
    daily_page_id: str,
) -> str:
    """Garmin日次の同日レコード。なければ作成。Daily Relation を必ずセット。"""
    pid = find_page_by_date(client, garmin_db_id, GP_DATE, target_date)
    if pid:
        # 既存：Daily Relation だけ最新化（万が一外れている場合の保険）
        client.pages.update(
            page_id=pid,
            properties={GP_DAILY_REL: {"relation": [{"id": daily_page_id}]}},
        )
        return pid
    response = client.pages.create(
        parent={"database_id": garmin_db_id},
        properties={
            GP_TITLE:      {"title":    [{"text": {"content": f"Garmin {target_date}"}}]},
            GP_DATE:       {"date":     {"start": target_date}},
            GP_DAILY_REL:  {"relation": [{"id":   daily_page_id}]},
        },
        icon={"emoji": "⌚"},
    )
    return response["id"]


def update_garmin(client: Client, page_id: str, props: dict) -> None:
    if not props:
        return
    client.pages.update(page_id=page_id, properties=props)


# ──────────────────────────────────────────────────────
# Garmin → Notion 同期
# ──────────────────────────────────────────────────────

def sync_sleep(garmin: Garmin, client: Client, garmin_page_id: str, target_date: str) -> None:
    data = garmin.get_sleep_data(target_date)
    dto  = data.get("dailySleepDTO", {})
    if not dto:
        print("⚠️ 睡眠データなし")
        return

    deep  = (dto.get("deepSleepSeconds")  or 0) / 3600
    light = (dto.get("lightSleepSeconds") or 0) / 3600
    rem   = (dto.get("remSleepSeconds")   or 0) / 3600
    total = deep + light + rem
    rhr   = data.get("restingHeartRate", 0) or 0

    score_obj   = dto.get("sleepScores") or {}
    sleep_score = (
        score_obj.get("overall", {}).get("value") if isinstance(score_obj, dict) else None
    )

    props = {
        GP_TOTAL_SLEEP: {"number": round(total, 1)},
        GP_DEEP_SLEEP:  {"number": round(deep,  1)},
        GP_LIGHT_SLEEP: {"number": round(light, 1)},
        GP_REM_SLEEP:   {"number": round(rem,   1)},
        GP_RHR:         {"number": rhr},
    }
    if sleep_score is not None:
        props[GP_SLEEP_SCORE] = {"number": sleep_score}

    update_garmin(client, garmin_page_id, props)
    print(f"✅ 睡眠データ更新：合計 {round(total, 1)}h / スコア {sleep_score}")


def sync_hrv(garmin: Garmin, client: Client, garmin_page_id: str, target_date: str) -> None:
    try:
        data    = garmin.get_hrv_data(target_date)
        hrv_val = None
        if data and "hrvSummary" in data:
            hrv_val = data["hrvSummary"].get("lastNight") or data["hrvSummary"].get("weeklyAvg")
        if hrv_val:
            update_garmin(client, garmin_page_id, {GP_HRV: {"number": hrv_val}})
            print(f"✅ HRV：{hrv_val}")
    except Exception as e:
        print(f"⚠️ HRVデータ取得失敗：{e}")


def sync_body_battery(garmin: Garmin, client: Client, garmin_page_id: str, target_date: str) -> None:
    try:
        data = garmin.get_body_battery(target_date)
        if not data:
            return
        values = [d.get("charged") or d.get("bodyBatteryLevel", 0) for d in data if d]
        values = [v for v in values if v]
        if not values:
            return
        bb_high = max(values)
        bb_low  = min(values)
        update_garmin(client, garmin_page_id, {
            GP_BB_HIGHEST: {"number": bb_high},
            GP_BB_LOWEST:  {"number": bb_low},
        })
        print(f"✅ Body Battery：最高 {bb_high} / 最低 {bb_low}")
    except Exception as e:
        print(f"⚠️ Body Batteryデータ取得失敗：{e}")


def sync_steps(garmin: Garmin, client: Client, garmin_page_id: str, target_date: str) -> None:
    try:
        data        = garmin.get_steps_data(target_date)
        total_steps = sum(d.get("steps", 0) or 0 for d in data) if data else 0
        if total_steps > 0:
            update_garmin(client, garmin_page_id, {GP_STEPS: {"number": total_steps}})
            print(f"✅ 歩数：{total_steps:,}")
    except Exception as e:
        print(f"⚠️ 歩数データ取得失敗：{e}")


# ──────────────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────────────

def main() -> None:
    target_date = (date.today() - timedelta(days=1)).isoformat()

    # 環境変数：旧 NOTION_SLEEP_DB_ID も後方互換で受け付ける
    garmin_db_id = os.getenv("NOTION_GARMIN_DB_ID") or os.getenv("NOTION_SLEEP_DB_ID")
    daily_db_id  = os.getenv("NOTION_DAILY_DB_ID")
    notion_token = os.getenv("NOTION_TOKEN")

    if not notion_token:
        raise RuntimeError("NOTION_TOKEN を設定してください")
    if not garmin_db_id:
        raise RuntimeError("NOTION_GARMIN_DB_ID（または旧 NOTION_SLEEP_DB_ID）を設定してください")
    if not daily_db_id:
        raise RuntimeError("NOTION_DAILY_DB_ID を設定してください（日次ジャーナル DB の ID）")

    client = Client(auth=notion_token)
    garmin = get_garmin_client()

    print(f"\n📅 同期対象日：{target_date}")

    # 1) 日次ジャーナル DB のエントリを確保（無ければスタブ作成）
    daily_id = ensure_daily_page(client, daily_db_id, target_date)
    print(f"  日次ジャーナル page_id：{daily_id[:8]}…")

    # 2) Garmin日次 DB のレコードを確保（Daily Relation を必ずセット）
    garmin_id = ensure_garmin_page(client, garmin_db_id, target_date, daily_id)
    print(f"  Garmin日次 page_id：{garmin_id[:8]}…")

    # 3) 各種データを Garmin日次レコードに書き込み
    sync_sleep(garmin, client, garmin_id, target_date)
    sync_hrv(garmin, client, garmin_id, target_date)
    sync_body_battery(garmin, client, garmin_id, target_date)
    sync_steps(garmin, client, garmin_id, target_date)

    print("\n🎉 同期完了")


if __name__ == "__main__":
    main()
