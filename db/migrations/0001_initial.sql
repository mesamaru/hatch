-- pterodeploy 初期スキーマ
-- 方針: DB が唯一の正本。外部サービス（パネル・Cloudflare・Kuma・edge）はここから「あるべき状態」を作る。
-- 時刻はすべて timestamptz（UTC で保存し、表示時に Asia/Tokyo へ変換）。

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- プラン（料金は将来用。今は NULL = 無料）
-- ---------------------------------------------------------------------------
CREATE TABLE plans (
  id              text PRIMARY KEY,                 -- 'light', 'standard'
  name            text NOT NULL,
  memory_mb       integer NOT NULL CHECK (memory_mb > 0),
  cpu_percent     integer NOT NULL CHECK (cpu_percent > 0),   -- 100 = 1コア
  disk_mb         integer NOT NULL CHECK (disk_mb > 0),
  backup_limit    integer NOT NULL DEFAULT 3,
  period_days     integer NOT NULL DEFAULT 30,
  price_jpy       integer,                          -- 将来の料金。NULL は無料
  is_active       boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- ユーザー
-- ---------------------------------------------------------------------------
CREATE TABLE users (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username          text NOT NULL UNIQUE CHECK (username ~ '^[a-z0-9][a-z0-9_.-]{2,31}$'),
  email             text NOT NULL UNIQUE,
  discord_id        text UNIQUE,
  role              text NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
  status            text NOT NULL DEFAULT 'active' CHECK (status IN ('invited','active','suspended','deleting','deleted')),
  panel_user_id     integer UNIQUE,                 -- Pterodactyl / Pelican 側のユーザーID
  panel_synced_at   timestamptz,
  totp_enabled      boolean NOT NULL DEFAULT false, -- 管理者は必須（アプリ側で強制）
  max_servers       integer NOT NULL DEFAULT 1,
  suspend_reason    text,
  -- 退会：申請から7日後に削除（期間中は取り消し可）。削除後は個人情報を消した行だけを残す
  deletion_requested_at timestamptz,
  deletion_due_at       timestamptz,
  deleted_at            timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CHECK ((status = 'deleting') = (deletion_due_at IS NOT NULL AND deleted_at IS NULL))
);
CREATE INDEX users_deletion_due ON users (deletion_due_at) WHERE status = 'deleting';

-- Discord ロールとパネル上の扱いの対応
CREATE TABLE discord_role_rules (
  discord_role_id   text PRIMARY KEY,
  label             text NOT NULL,
  grants_role       text NOT NULL DEFAULT 'user' CHECK (grants_role IN ('user','admin')),
  plan_id           text REFERENCES plans(id),
  max_servers       integer NOT NULL DEFAULT 1,
  own_slot_rules    integer NOT NULL DEFAULT 1       -- 自動で作る専用アドレス枠の数
);

-- 利用規約（版ごと）と同意の記録
CREATE TABLE tos_versions (
  version         text PRIMARY KEY,                 -- '2026-10-01'
  body_md         text NOT NULL,
  published_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tos_acceptances (
  user_id         uuid NOT NULL REFERENCES users(id),
  tos_version     text NOT NULL REFERENCES tos_versions(version),
  accepted_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, tos_version)
);

-- ---------------------------------------------------------------------------
-- インフラ
-- ---------------------------------------------------------------------------
CREATE TABLE nodes (
  id                  text PRIMARY KEY,             -- 'node-tokyo-1'
  panel_node_id       integer NOT NULL UNIQUE,
  tailscale_ip        inet NOT NULL UNIQUE,
  memory_mb           integer NOT NULL,
  disk_mb             integer NOT NULL,
  overcommit_percent  integer NOT NULL DEFAULT 90,  -- 割当がこの割合を超えたら配置しない
  accepting           boolean NOT NULL DEFAULT true,
  created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE edges (
  id                text PRIMARY KEY,               -- 'edge-1'
  public_ip         inet NOT NULL,
  tailscale_ip      inet NOT NULL,
  is_active         boolean NOT NULL DEFAULT false, -- edge.example.net が向いている方
  applied_version   bigint,                         -- エージェントが適用した設定の版
  last_seen_at      timestamptz,
  last_error        text
);
-- 使用中の edge は常に1台だけ
CREATE UNIQUE INDEX edges_one_active ON edges (is_active) WHERE is_active;

-- edge 設定の版（エージェントはこれを取りに来る）
CREATE TABLE edge_config_versions (
  version         bigserial PRIMARY KEY,
  haproxy_cfg     text NOT NULL,
  nft_rules       text NOT NULL DEFAULT '',         -- UDP の転送（nftables）
  content_hash    text NOT NULL,                    -- 内容が同じなら新しい版を作らない
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- ドメイン・IP・アドレス枠
--   ドメイン     : Cloudflare のゾーン（例 nuids.jp）。複数登録できる
--   IP アドレス  : edge などの公開 IP
--   IP の紐付け   : ホスト名 → IP（A レコード）。edge.<ドメイン> は「使用中の edge に追従」
--   アドレス枠    : ポート範囲とホスト名の対応ルール（例 25560-25569 ↔ mc{nn}trt.nuids.jp）
--   スロット      : 枠から展開した「ポート 1つ ↔ ホスト名 1つ」
-- ---------------------------------------------------------------------------
CREATE TABLE domains (
  id            serial PRIMARY KEY,
  name          text NOT NULL UNIQUE CHECK (name ~ '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$'),
  cf_zone_id    text NOT NULL,
  edge_host     text NOT NULL DEFAULT 'edge',        -- edge.<name> の A レコードを管理する
  is_default    boolean NOT NULL DEFAULT false,
  verified_at   timestamptz,                         -- ゾーンへの接続確認に成功した時刻
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX domains_one_default ON domains (is_default) WHERE is_default;

CREATE TABLE ip_addresses (
  id          serial PRIMARY KEY,
  label       text NOT NULL,
  address     inet NOT NULL UNIQUE,
  edge_id     text REFERENCES edges(id),             -- edge の IP なら紐付ける
  note        text
);

CREATE TABLE ip_bindings (
  id                  serial PRIMARY KEY,
  domain_id           integer NOT NULL REFERENCES domains(id),
  host                text NOT NULL CHECK (host = '@' OR host ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'),
  ip_id               integer REFERENCES ip_addresses(id),
  follow_active_edge  boolean NOT NULL DEFAULT false, -- true なら ip_id は使わず、使用中の edge の IP を向く
  cf_record_id        text,
  synced_at           timestamptz,
  UNIQUE (domain_id, host),
  CHECK (follow_active_edge OR ip_id IS NOT NULL)
);

CREATE TABLE slot_rules (
  id             serial PRIMARY KEY,
  name           text NOT NULL,
  domain_id      integer NOT NULL REFERENCES domains(id),
  port_start     integer NOT NULL CHECK (port_start BETWEEN 1024 AND 65535),
  port_end       integer NOT NULL CHECK (port_end BETWEEN 1024 AND 65535),
  host_template  text NOT NULL,                     -- 'mc{nn}trt' / '{server}' / 'srv{port}'
  number_start   integer NOT NULL DEFAULT 0,        -- {n} = number_start + (port - port_start)
  excluded_ports integer[] NOT NULL DEFAULT '{}',
  record_mode    text NOT NULL DEFAULT 'cname_edge' CHECK (record_mode IN ('cname_edge','a_ip')),
  ip_id          integer REFERENCES ip_addresses(id),
  create_srv     boolean NOT NULL DEFAULT true,
  prepublish     boolean NOT NULL DEFAULT true,     -- 割り当て前に DNS を作っておく（固定ホスト名のみ）
  assign_to      text NOT NULL DEFAULT 'shared' CHECK (assign_to IN ('shared','user')),
  user_id        uuid REFERENCES users(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  CHECK (port_end >= port_start AND port_end - port_start < 200),   -- 1つの枠は200ポートまで
  CHECK (record_mode <> 'a_ip' OR ip_id IS NOT NULL),
  CHECK (assign_to <> 'user' OR user_id IS NOT NULL),
  CHECK (host_template NOT LIKE '%{server}%' OR NOT prepublish),
  CONSTRAINT slot_rules_no_overlap EXCLUDE USING gist (int4range(port_start, port_end, '[]') WITH &&)
);

CREATE TABLE slots (
  port        integer PRIMARY KEY,                  -- 同じポートは全体で1つだけ
  rule_id     integer NOT NULL REFERENCES slot_rules(id),
  domain_id   integer NOT NULL REFERENCES domains(id),
  host        text,                                 -- NULL はサーバー名を使う枠（{server}）
  status      text NOT NULL DEFAULT 'free' CHECK (status IN ('free','assigned','held','disabled')),
  server_id   uuid,                                 -- servers 作成後に外部キーを付ける（下）
  dns_state   text NOT NULL DEFAULT 'none' CHECK (dns_state IN ('none','published','error')),
  dns_error   text,
  CHECK ((status IN ('assigned','held')) = (server_id IS NOT NULL))
);
CREATE UNIQUE INDEX slots_host_unique ON slots (domain_id, host) WHERE host IS NOT NULL;

-- 誰も使えない予約ポート（範囲）
CREATE TABLE reserved_ports (
  start_port  integer NOT NULL,
  end_port    integer NOT NULL,
  reason      text NOT NULL,
  PRIMARY KEY (start_port, end_port),
  CHECK (end_port >= start_port)
);

-- サーバー名・ホスト名として使えない名前
CREATE TABLE reserved_names (
  name   text PRIMARY KEY
);
INSERT INTO reserved_names(name) VALUES
  ('www'),('edge'),('edge-1'),('edge-2'),('panel'),('gp'),('api'),('status'),('mail'),('admin'),('play'),('kuma'),('dev'),('_minecraft');

-- ---------------------------------------------------------------------------
-- サーバー
-- ---------------------------------------------------------------------------
CREATE TABLE servers (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name              text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$'),   -- 3〜32文字
  owner_id          uuid NOT NULL REFERENCES users(id),
  plan_id           text NOT NULL REFERENCES plans(id),
  game              text NOT NULL,                  -- 'storiamc', 'paper', 'velocity', 'palworld' ...
  node_id           text REFERENCES nodes(id),
  panel_server_id   integer UNIQUE,
  panel_uuid        uuid UNIQUE,
  panel_allocation_id integer UNIQUE,
  fqdn              text,                           -- 接続アドレス（例 mc03trt.nuids.jp）
  exposed           boolean NOT NULL DEFAULT true,  -- false = edge で公開しない（Velocity 配下など）
  status            text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','provisioning','running','stopped','suspended','maintenance','trashed','purging','purged','failed')),
  is_dev_copy       boolean NOT NULL DEFAULT false,
  source_server_id  uuid REFERENCES servers(id),    -- 開発用コピーの元
  proxy_server_id   uuid REFERENCES servers(id),    -- Velocity 配下の場合のプロキシ
  auto_restart      boolean NOT NULL DEFAULT true,
  public_status     boolean NOT NULL DEFAULT false,
  maintenance_until timestamptz,
  expires_at        timestamptz NOT NULL,
  trashed_at        timestamptz,
  purge_after       timestamptz,                    -- ゴミ箱から完全削除する時刻
  suspend_reason    text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CHECK (proxy_server_id IS NULL OR proxy_server_id <> id)
);
-- 名前は完全削除されるまで再利用できない（ゴミ箱から戻せるように）
-- 作成に失敗したもの（failed）と完全削除したもの（purged）の名前は再利用できる
CREATE UNIQUE INDEX servers_name_unique ON servers (name) WHERE status NOT IN ('purged', 'failed');
CREATE UNIQUE INDEX servers_fqdn_unique ON servers (fqdn) WHERE status NOT IN ('purged', 'failed') AND fqdn IS NOT NULL;
CREATE INDEX servers_owner ON servers (owner_id);
ALTER TABLE slots ADD CONSTRAINT slots_server_fk FOREIGN KEY (server_id) REFERENCES servers(id);
CREATE UNIQUE INDEX slots_one_per_server ON slots (server_id) WHERE server_id IS NOT NULL;
CREATE INDEX servers_expires ON servers (expires_at) WHERE status IN ('running','stopped','maintenance','suspended');

-- 共同管理者
CREATE TABLE server_shares (
  server_id     uuid NOT NULL REFERENCES servers(id),
  user_id       uuid NOT NULL REFERENCES users(id),
  permission    text NOT NULL CHECK (permission IN ('view','console','files','full')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (server_id, user_id)
);

-- 独自ドメイン
CREATE TABLE custom_domains (
  hostname      text PRIMARY KEY,
  server_id     uuid NOT NULL REFERENCES servers(id),
  verify_token  text NOT NULL,
  status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','broken')),
  checked_at    timestamptz
);

-- 外部サービスに作ったものの記録（整合性チェックと削除に使う）
CREATE TABLE dns_records (
  cf_record_id  text PRIMARY KEY,
  domain_id     integer NOT NULL REFERENCES domains(id),
  server_id     uuid REFERENCES servers(id),        -- NULL は事前作成したスロットや edge などの基盤用
  slot_port     integer,                            -- スロットのレコードならそのポート
  type          text NOT NULL CHECK (type IN ('A','CNAME','SRV','TXT')),
  name          text NOT NULL,
  content       text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE monitors (
  kuma_monitor_id integer PRIMARY KEY,
  server_id       uuid NOT NULL UNIQUE REFERENCES servers(id),
  kind            text NOT NULL CHECK (kind IN ('gamedig','tcp','push')),
  push_token      text,
  paused          boolean NOT NULL DEFAULT false
);

-- 自動再起動の履歴（1時間に3回までの判定に使う）
CREATE TABLE auto_restarts (
  id          bigserial PRIMARY KEY,
  server_id   uuid NOT NULL REFERENCES servers(id),
  at          timestamptz NOT NULL DEFAULT now(),
  result      text NOT NULL
);
CREATE INDEX auto_restarts_recent ON auto_restarts (server_id, at DESC);

-- 通知済みの記録（期限通知などを二重に送らない）
CREATE TABLE notifications_sent (
  server_id   uuid NOT NULL REFERENCES servers(id),
  kind        text NOT NULL,                        -- 'expiry_7d', 'expiry_3d', 'expiry_1d', 'disk_90'
  period_key  text NOT NULL,                        -- 期限日など。延長されると新しい key になる
  sent_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (server_id, kind, period_key)
);


-- 期限延長の申請
CREATE TABLE extension_requests (
  id           bigserial PRIMARY KEY,
  server_id    uuid NOT NULL REFERENCES servers(id),
  requested_by uuid NOT NULL REFERENCES users(id),
  days         integer NOT NULL DEFAULT 30 CHECK (days BETWEEN 1 AND 365),
  status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  decided_by   uuid REFERENCES users(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  decided_at   timestamptz
);
CREATE UNIQUE INDEX extension_requests_one_pending ON extension_requests (server_id) WHERE status = 'pending';

-- 独自プラグインの記録（パネル上のファイルと突き合わせる）
CREATE TABLE server_plugins (
  server_id    uuid NOT NULL REFERENCES servers(id),
  source       text NOT NULL CHECK (source IN ('modrinth','hangar','manual')),
  project_id   text NOT NULL,
  name         text NOT NULL,
  version_id   text,
  file_name    text NOT NULL,
  installed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (server_id, source, project_id)
);

-- ---------------------------------------------------------------------------
-- ジョブ（デプロイ・削除など）。Postgres だけでキューを実現（SKIP LOCKED）
-- ---------------------------------------------------------------------------
CREATE TABLE jobs (
  id               bigserial PRIMARY KEY,
  kind             text NOT NULL,                   -- 'deploy', 'trash', 'restore', 'purge', 'dev_copy' ...
  idempotency_key  text UNIQUE,                     -- Discord のインタラクションIDなど。二重実行を防ぐ
  requested_by     uuid REFERENCES users(id),
  via              text NOT NULL CHECK (via IN ('web','discord','system')),
  server_id        uuid REFERENCES servers(id),
  params           jsonb NOT NULL DEFAULT '{}',
  dry_run          boolean NOT NULL DEFAULT false,
  status           text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','rolled_back','cancelled')),
  current_step     text,
  error            text,
  run_after        timestamptz NOT NULL DEFAULT now(),
  locked_by        text,
  locked_at        timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz,
  attempts         integer NOT NULL DEFAULT 0,      -- 取り出された回数（ワーカーが落ちて再開した回数を含む）
  discord_message  jsonb                            -- 進捗を書き換える Discord メッセージの場所
);
CREATE INDEX jobs_pick ON jobs (run_after) WHERE status = 'queued';

CREATE TABLE job_steps (
  job_id       bigint NOT NULL REFERENCES jobs(id),
  seq          integer NOT NULL,
  name         text NOT NULL,
  status       text NOT NULL CHECK (status IN ('pending','running','done','failed','undone','skipped')),
  undo         jsonb,                               -- 巻き戻しに必要な情報（作ったIDなど）
  error        text,                                -- 失敗の内容（日本語）
  note         text,                                -- 飛ばした理由（「作成済み」など）
  started_at   timestamptz,
  finished_at  timestamptz,
  PRIMARY KEY (job_id, seq)
);

-- ---------------------------------------------------------------------------
-- お知らせ・違反対応
-- ---------------------------------------------------------------------------
CREATE TABLE announcements (
  id           bigserial PRIMARY KEY,
  title        text NOT NULL,
  body         text NOT NULL,
  severity     text NOT NULL DEFAULT 'info' CHECK (severity IN ('info','warning','outage')),
  audience     jsonb NOT NULL DEFAULT '{"all":true}',   -- {"all":true} / {"nodes":["node-tokyo-1"]}
  post_discord boolean NOT NULL DEFAULT true,
  created_by   uuid REFERENCES users(id),
  starts_at    timestamptz NOT NULL DEFAULT now(),
  ends_at      timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE violations (
  id          bigserial PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES users(id),
  server_id   uuid REFERENCES servers(id),
  reason      text NOT NULL,
  action      text NOT NULL CHECK (action IN ('warning','suspend_server','suspend_user')),
  created_by  uuid NOT NULL REFERENCES users(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz
);

-- ---------------------------------------------------------------------------
-- 操作ログ（追記専用。更新・削除はトリガーで禁止）
-- ---------------------------------------------------------------------------
CREATE TABLE audit_log (
  id          bigserial PRIMARY KEY,
  at          timestamptz NOT NULL DEFAULT now(),
  actor_id    uuid REFERENCES users(id),            -- NULL = システム
  via         text NOT NULL CHECK (via IN ('web','discord','system','cli')),
  action      text NOT NULL,
  target      text,
  server_id   uuid REFERENCES servers(id),
  job_id      bigint REFERENCES jobs(id),
  result      text NOT NULL DEFAULT 'ok',
  detail      jsonb
);
CREATE INDEX audit_log_at ON audit_log (at DESC);
CREATE INDEX audit_log_server ON audit_log (server_id, at DESC);

CREATE FUNCTION audit_log_readonly() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  -- 1年を過ぎた行の削除だけは保守ジョブに許可する
  IF TG_OP = 'DELETE' AND OLD.at < now() - interval '1 year' THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'audit_log は追記専用です';
END $$;
CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_readonly();


-- ---------------------------------------------------------------------------
-- ログインセッション・設定・通知
-- ---------------------------------------------------------------------------
CREATE TABLE sessions (
  id            text PRIMARY KEY,                    -- Cookie の値（32バイトの乱数）の SHA-256。DB が漏れてもセッションを奪えない
  user_id       uuid NOT NULL REFERENCES users(id),
  csrf_token    text NOT NULL,
  totp_ok       boolean NOT NULL DEFAULT false,      -- 管理者の二段階認証を通過したか
  totp_failures integer NOT NULL DEFAULT 0,          -- 5回続けて間違えたらセッションを消す
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz NOT NULL,
  user_agent    text,
  ip            inet
);
CREATE INDEX sessions_user ON sessions (user_id);

CREATE TABLE user_totp (
  user_id     uuid PRIMARY KEY REFERENCES users(id),
  secret_enc  text NOT NULL,                         -- PD_SECRET_KEY で暗号化
  last_counter bigint NOT NULL DEFAULT 0,            -- 最後に使ったコードの時刻枠（同じコードの再利用を防ぐ）
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE notification_prefs (
  user_id     uuid PRIMARY KEY REFERENCES users(id),
  down        boolean NOT NULL DEFAULT true,
  expiry      boolean NOT NULL DEFAULT true,
  disk        boolean NOT NULL DEFAULT true
);


-- 表示の設定（テーマ・ガラス・背景）
CREATE TABLE user_prefs (
  user_id     uuid PRIMARY KEY REFERENCES users(id),
  theme       text NOT NULL DEFAULT 'auto' CHECK (theme IN ('auto','light','dark')),
  glass       numeric(3,2) NOT NULL DEFAULT 0.72 CHECK (glass BETWEEN 0.50 AND 0.97),
  -- 背景：{"kind":"default"} / {"kind":"preset","id":"aurora"} / {"kind":"upload","upload_id":"uuid"}
  background  jsonb NOT NULL DEFAULT '{"kind":"default"}',
  bg_dim      numeric(3,2) NOT NULL DEFAULT 0.35 CHECK (bg_dim BETWEEN 0 AND 0.85),   -- 背景を暗く（明るく）する量
  bg_blur     smallint NOT NULL DEFAULT 0 CHECK (bg_blur BETWEEN 0 AND 40),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- アップロードしたファイル（背景画像など）。実体は PD_DATA_DIR/uploads/ に保存
CREATE TABLE uploads (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id),
  kind        text NOT NULL CHECK (kind IN ('background')),
  path        text NOT NULL UNIQUE,         -- PD_DATA_DIR からの相対パス
  mime        text NOT NULL CHECK (mime IN ('image/webp','image/jpeg')),
  bytes       integer NOT NULL CHECK (bytes > 0 AND bytes <= 3145728),
  width       integer NOT NULL,
  height      integer NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX uploads_user ON uploads (user_id);

-- 自己監視：各プロセスの生存報告（30秒ごとに更新）
CREATE TABLE component_heartbeats (
  component   text PRIMARY KEY CHECK (component IN ('api','worker','scheduler','bot')),
  instance    text NOT NULL,                -- ホスト名とプロセスID
  version     text NOT NULL,
  beat_at     timestamptz NOT NULL DEFAULT now(),
  detail      jsonb
);

-- 違反で停止したアカウントが退会・再登録で逃れないように、Discord ID のハッシュだけを残す
CREATE TABLE banned_identities (
  discord_id_hash text PRIMARY KEY,         -- HMAC-SHA256(PD_SECRET_KEY, discord_id)
  reason          text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  expires_at      timestamptz
);

-- 画面から変える設定（キーは docs/IMPLEMENTATION.md の一覧のみ）
CREATE TABLE app_settings (
  key         text PRIMARY KEY,
  value       jsonb NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO app_settings (key, value) VALUES
  ('trash_hours', '72'),
  ('expiry_grace_days', '14'),
  ('final_backup_keep_days', '30'),
  ('auto_restart_max_per_hour', '3'),
  ('tos_current_version', 'null'),
  ('account_deletion_days', '7'),
  ('default_background', '{"kind":"preset","id":"aurora"}'),
  ('default_bg_dim', '0.35');

-- ---------------------------------------------------------------------------
-- 初期データ
-- ---------------------------------------------------------------------------
INSERT INTO plans (id, name, memory_mb, cpu_percent, disk_mb, backup_limit, period_days) VALUES
  ('light',    'Light',    2048, 100, 10240, 3, 30),
  ('standard', 'Standard', 4096, 200, 20480, 5, 30);

INSERT INTO reserved_ports (start_port, end_port, reason) VALUES
  (1, 1023, 'システム用');
-- 25565（Minecraft の既定ポート）は予約しない。枠に含めると、そのホスト名は SRV なしでも接続できる。
