-- edge の Tailscale の IP は任意にする（edge から最初の報告が来たときに自動で入る）。T48 の続き。
ALTER TABLE edges ALTER COLUMN tailscale_ip DROP NOT NULL;
