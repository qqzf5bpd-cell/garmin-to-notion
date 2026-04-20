"""
Garmin OAuth2 トークン生成スクリプト（garminconnect経由版）
=============================================================
このスクリプトはローカル環境で1回だけ実行します。
生成したトークンを GitHub Secrets > GARMIN_TOKENS に設定することで、
GitHub Actions が MFA なしで Garmin にログインできるようになります。

使い方:
  pip install garminconnect garth
  python get_garmin_tokens.py
"""

from garminconnect import Garmin
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
        print("✉️  Garminから2段階認証が求められています。")
        print("   認証アプリ（Google Authenticator等）の6桁コードを入力してください。")
        return input("MFAコード: ").strip()

    try:
        garmin = Garmin(
            email=email,
            password=password,
            prompt_mfa=prompt_mfa
        )
        garmin.login()
    except Exception as e:
        print(f"\n❌ ログイン失敗: {e}")
        print()
        print("【対処法】")
        print("・429エラー: 30分以上待ってから再実行してください")
        print("・MFAエラー: 認証アプリのコードをご確認ください")
        print("・403エラー: Garminサイト(connect.garmin.com)にブラウザでログインしてから再試行")
        sys.exit(1)

    print("✅ ログイン成功！")
    print()

    # garth経由でトークンを取得
    try:
        tokens = garmin.garth.dumps()
    except AttributeError:
        # garminconnect 0.3.x では garth 属性名が変わっている可能性
        import garth
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
    print("4. Secret: 上記 === の間の文字列をすべてコピー＆ペースト")
    print("5. 'Add secret' をクリックして保存")
    print()
    print("6. GitHub Actions > 'Run workflow' で動作確認")
    print()


if __name__ == "__main__":
    main()
