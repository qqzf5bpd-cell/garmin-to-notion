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
