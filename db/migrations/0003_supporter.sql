-- 権限を「管理者・サポーター・利用者」の3段階にする。
-- サポーターは、管理者が割り当てたサーバーを閲覧し、電源操作でサポートできる（自分のサーバーも作れる）。
ALTER TABLE users DROP CONSTRAINT users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('user','supporter','admin'));

ALTER TABLE discord_role_rules DROP CONSTRAINT discord_role_rules_grants_role_check;
ALTER TABLE discord_role_rules
  ADD CONSTRAINT discord_role_rules_grants_role_check CHECK (grants_role IN ('user','supporter','admin'));

-- サポーターに割り当てたサーバー
CREATE TABLE server_supporters (
  server_id     uuid NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  user_id       uuid NOT NULL REFERENCES users(id),
  assigned_by   uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (server_id, user_id)
);
CREATE INDEX server_supporters_user ON server_supporters (user_id);
