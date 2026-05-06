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

def get_garmin_client() -> Garmin:
    """OAuth2 トークンがあればそれを使い、なければメール/パスワードでログイン。"""
    tokens = os.getenv("GARMIN_TOKENS")
    if tokens:
        try:
            try:
                raw = base64.b64decode(tokens).decode()
            except Exception:
                raw = tokens
            token_data = json.loads(raw)
            oauth2_data = token_data.get("oauth2_token")
            if not oauth2_data:
                raise ValueError("OAuth2 トークンが見つかりません")
            garth.client.oauth2_token = OAuth2Token(**oauth2_data)
            g = Garmin()
            g.garth = garth.client
            print("✅ 保存済みトークンでログイン成功")
            return g
        except Exception as e:
            print(f"⚠️ トークンロード失敗: {e}、パスワードでログインを試行...")

    # フォールバック：メール/パスワード
    def prompt_mfa():
        raise RuntimeError("MFA が必要です。get_garmin_tokens.py でトークンを生成してください")

    g = Garmin(
        email=os.getenv("GARMIN_EMAIL"),
        password=os.getenv("GARMIN_PASSWORD"),
        prompt_mfa=prompt_mfa,
    )
    g.login()
    print("✅ パスワードでログイン成功")
    return g


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
