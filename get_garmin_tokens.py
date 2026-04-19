"""
Garmin OAuth2 トークン生成スクリプト
====================================
このスクリプトはローカル環境で1回だけ実行します。
生成したトークンを GitHub Secrets > GARMIN_TOKENS に設定することで、
GitHub Actions が MFA なしで Garmin にログインできるようになります。

使い方:
  pip install garminconnect garth
  python get_garmin_tokens.py
"""

from garminconnect import Garmin
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

    mfa_prompted = {"done": False}

    def prompt_mfa():
        mfa_prompted["done"] = True
        return input("MFA コード（認証アプリの6桁）: ").strip()

    print()
    print("Garminにログイン中...")

    try:
        g = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
        g.login()
    except Exception as e:
        print(f"❌ ログイン失敗: {e}")
        sys.exit(1)

    print("✅ ログイン成功！")
    print()

    # トークンを base64 文字列として取得
    tokens = garth.client.dumps()

    print("=" * 60)
    print("以下のトークン文字列を GARMIN_TOKENS として設定してください")
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
    print()
    print("3. 入力:")
    print("   Name  : GARMIN_TOKENS")
    print("   Secret: 上記の文字列（===の間の部分）をすべてコピー＆ペースト")
    print()
    print("4. 'Add secret' をクリックして保存")
    print()
    print("5. GitHub Actions > 'Run workflow' で動作確認")
    print()


if __name__ == "__main__":
    main()
