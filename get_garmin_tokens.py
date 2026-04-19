"""
Garmin OAuth2 トークン生成スクリプト（garth直接使用版）
========================================================
garminconnect 0.3.x では garth 経由ではなく
garth ライブラリを直接使ってトークンを生成します。

使い方:
  pip install garth
  python get_garmin_tokens.py

生成されたトークンを GitHub Secrets > GARMIN_TOKENS に設定してください。
"""

import garth
import getpass
import sys


def main():
    print("=" * 60)
    print("  Garmin OAuth2 トークン生成ツール")
    print("=" * 60)
    print()

    email    = input("Garmin Connect メールアドレス: ").strip()
    password = getpass.getpass("Garmin Connect パスワード: ")

    print()
    print("Garminにログイン中...")

    def prompt_mfa():
        print()
        print("Garminから2段階認証が求められています。")
        return input("認証アプリの6桁コードを入力してください: ").strip()

    try:
        garth.configure(domain="garmin.com")
        garth.login(email, password, prompt_mfa=prompt_mfa)
    except TypeError:
        # 古いバージョンの garth は prompt_mfa 非対応
        try:
            garth.login(email, password)
        except Exception as e:
            if "NeedsMFAToken" in str(type(e).__name__) or "mfa" in str(e).lower():
                mfa = input("認証アプリの6桁コード: ").strip()
                garth.login(email, password, mfa_token=mfa)
            else:
                raise
    except Exception as e:
        print(f"\n❌ ログイン失敗: {e}")
        print()
        print("【よくある原因】")
        print("・429エラー: 短時間に何度もログイン試行した場合。15〜30分待ってから再実行してください。")
        print("・MFAエラー: Garminの2段階認証が有効な場合は認証アプリのコードが必要です。")
        sys.exit(1)

    print("✅ ログイン成功！")
    print()

    tokens = garth.client.dumps()

    print("=" * 60)
    print("以下の文字列を GARMIN_TOKENS として GitHub Secrets に設定してください")
    print("=" * 60)
    print()
    print(tokens)
    print()
    print("=" * 60)
    print()
    print("【GitHub Secrets への登録手順】")
    print("1. 以下の URL を開く:")
    print("   https://github.com/qqzf5bpd-cell/garmin-to-notion/settings/secrets/actions")
    print()
    print("2. 'New repository secret' をクリック")
    print("3. Name  : GARMIN_TOKENS")
    print("4. Secret: 上記の === で囲まれた文字列をすべてコピー＆ペースト")
    print("5. 'Add secret' をクリックして保存")
    print()
    print("6. GitHub Actions > 'Run workflow' で動作確認")
    print()


if __name__ == "__main__":
    main()
