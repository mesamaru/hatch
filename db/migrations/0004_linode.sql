-- Linode のアカウント（複数契約）と、Hatch が管理する Cloud Firewall。docs/SPEC.md 6B、IMPLEMENTATION.md 7A。
CREATE TABLE linode_accounts (
  id            serial PRIMARY KEY,
  label         text NOT NULL UNIQUE,
  token_enc     text NOT NULL,                    -- crypto.encrypt(token, "linode")
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE firewalls (
  id                  serial PRIMARY KEY,
  linode_account_id   integer NOT NULL REFERENCES linode_accounts(id),
  linode_firewall_id  bigint NOT NULL,
  label               text NOT NULL,
  synced_at           timestamptz,
  last_error          text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (linode_account_id, linode_firewall_id)
);

-- edge がどの契約の、どの Linode で、どのファイアウォールを使うか（共有・個別どちらも可）
ALTER TABLE edges ADD COLUMN linode_account_id integer REFERENCES linode_accounts(id);
ALTER TABLE edges ADD COLUMN linode_id bigint;
ALTER TABLE edges ADD COLUMN firewall_id integer REFERENCES firewalls(id);
