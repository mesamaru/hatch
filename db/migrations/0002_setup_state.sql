-- 初期設定画面の完了日時（null の間は、パネルを開くと初期設定画面になる）。
-- 既に管理者のロールが登録されている環境は、初期設定が済んでいるものとして扱う。
INSERT INTO app_settings (key, value)
SELECT 'setup_completed_at',
       CASE WHEN EXISTS (SELECT 1 FROM discord_role_rules WHERE grants_role = 'admin')
            THEN to_jsonb(now()) ELSE 'null'::jsonb END
ON CONFLICT (key) DO NOTHING;
