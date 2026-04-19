from datetime import datetime, date, timedelta
from garminconnect import Garmin
from notion_client import Client
from dotenv import load_dotenv
import os

load_dotenv()

def get_garmin_client():
    g = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    g.login()
    return g

def find_journal_entry(client, db_id, target_date: str):
    """日次ジャーナルDBから対象日付のエントリを検索"""
    # 日付プロパティ名は実際のDBに合わせて調整
    res = client.databases.query(
        database_id=db_id,
        filter={"property": "日付", "date": {"equals": target_date}}
    )
    results = res.get("results", [])
    return results[0]["id"] if results else None

def update_or_create_entry(client, db_id, target_date: str, props: dict):
    """エントリが存在すれば更新、なければ作成"""
    page_id = find_journal_entry(client, db_id, target_date)
    if page_id:
        client.pages.update(page_id=page_id, properties=props)
        print(f"✅ 更新: {target_date}")
    else:
        # 新規作成（タイトルも設定）
        props["日付"] = {"date": {"start": target_date}}
        client.pages.create(
            parent={"database_id": db_id},
            properties=props,
            icon={"emoji": "📔"}
        )
        print(f"✅ 新規作成: {target_date}")

def sync_sleep(garmin, client, db_id, target_date: str):
    data = garmin.get_sleep_data(target_date)
    dto = data.get("dailySleepDTO", {})
    if not dto:
        print("睡眠データなし")
        return

    deep   = (dto.get("deepSleepSeconds")  or 0) / 3600
    light  = (dto.get("lightSleepSeconds") or 0) / 3600
    rem    = (dto.get("remSleepSeconds")   or 0) / 3600
    total  = deep + light + rem
    rhr    = data.get("restingHeartRate", 0) or 0

    # 睡眠スコア（存在する場合）
    score_obj = dto.get("sleepScores") or {}
    sleep_score = score_obj.get("overall", {}).get("value") if isinstance(score_obj, dict) else None

    props = {
        "睡眠時間_Garmin": {"number": round(total, 1)},
        "深い睡眠":        {"number": round(deep,  1)},
        "浅い睡眠":        {"number": round(light, 1)},
        "レム睡眠":        {"number": round(rem,   1)},
        "安静時心拍数":    {"number": rhr},
    }
    if sleep_score is not None:
        props["睡眠スコア"] = {"number": sleep_score}

    update_or_create_entry(client, db_id, target_date, props)

def sync_hrv(garmin, client, db_id, target_date: str):
    try:
        data = garmin.get_hrv_data(target_date)
        hrv_val = None
        if data and "hrvSummary" in data:
            hrv_val = data["hrvSummary"].get("lastNight") or data["hrvSummary"].get("weeklyAvg")
        if hrv_val:
            page_id = find_journal_entry(client, db_id, target_date)
            if page_id:
                client.pages.update(page_id=page_id, properties={"HRV": {"number": hrv_val}})
                print(f"✅ HRV更新: {hrv_val}")
    except Exception as e:
        print(f"HRVデータ取得失敗: {e}")

def sync_body_battery(garmin, client, db_id, target_date: str):
    try:
        data = garmin.get_body_battery(target_date)
        if data and len(data) > 0:
            values = [d.get("charged") or d.get("bodyBatteryLevel", 0) for d in data if d]
            values = [v for v in values if v]
            if values:
                bb_start = values[0]
                bb_end   = values[-1]
                page_id = find_journal_entry(client, db_id, target_date)
                if page_id:
                    client.pages.update(page_id=page_id, properties={
                        "Body Battery 開始": {"number": bb_start},
                        "Body Battery 終了": {"number": bb_end},
                    })
                    print(f"✅ Body Battery: {bb_start} → {bb_end}")
    except Exception as e:
        print(f"Body Batteryデータ取得失敗: {e}")

def sync_steps(garmin, client, db_id, target_date: str):
    try:
        data = garmin.get_steps_data(target_date)
        total_steps = sum(d.get("steps", 0) or 0 for d in data) if data else 0
        if total_steps > 0:
            page_id = find_journal_entry(client, db_id, target_date)
            if page_id:
                client.pages.update(page_id=page_id, properties={"歩数": {"number": total_steps}})
                print(f"✅ 歩数: {total_steps:,}")
    except Exception as e:
        print(f"歩数データ取得失敗: {e}")

def main():
    target_date = (date.today() - timedelta(days=1)).isoformat()  # 昨日のデータ
    # 当日データが欲しい場合: target_date = date.today().isoformat()

    db_id  = os.getenv("NOTION_SLEEP_DB_ID")  # 日次ジャーナルDB ID
    client = Client(auth=os.getenv("NOTION_TOKEN"))
    garmin = get_garmin_client()

    print(f"📅 同期対象日: {target_date}")
    sync_sleep(garmin, client, db_id, target_date)
    sync_hrv(garmin, client, db_id, target_date)
    sync_body_battery(garmin, client, db_id, target_date)
    sync_steps(garmin, client, db_id, target_date)
    print("🎉 同期完了")

if __name__ == "__main__":
    main()
