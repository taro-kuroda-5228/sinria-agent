---
name: exbrain-vault-steward
description: Use when treating, reading, writing, or reorganizing the exbrain-vault shared knowledge hub (from Sinria or Claude Code in ANY project) — how to use the Sinria-centered 4-layer model, keep handoff.md synced, protect diaries/daily-logs, and run a safe non-destructive self-reorganization (dedup, digest, archive) behind a rollback tag.
version: 1.0.0
author: Sinria
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [exbrain-vault, knowledge-base, obsidian, sinria, handoff, self-improvement, vault-hygiene, memory]
    related_skills: [hermes-agent-skill-authoring, writing-plans]
---

# exbrain-vault Steward

`/Users/tarokuroda/exbrain-vault`（`~/exbrain-vault`）は Obsidian × Sinria × Claude Code が共有する
外部知識基盤。**どのディレクトリ／プロジェクトから起動しても、このスキルの規約が有効**。
Sinria と Claude Code は vault に対して同じ接し方をする。

このスキルは2部構成:
- **Part A — 日々の接し方**（読む／書く／同期する。全エージェント共通）
- **Part B — 自己整理（self-reorganization）手順**（Sinria が定期的に vault を整理・自己改善する）

---

## Part A — 日々の接し方

### 起動時・重要作業前に読む（必須）
重要タスク・意思決定・外部共有する成果物の前に、関係する範囲で以下を確認する:
1. `exbrain-vault/HOME.md`
2. `exbrain-vault/docs/START-HERE.md`
3. `exbrain-vault/workspaces/sinria/handoff.md` ← primary handoff（次セッションが最初に読む）
4. 関連する `exbrain-vault/wiki/decisions/`
5. 関連する `exbrain-vault/wiki/digests/`（raw の月次要約。生 raw を grep する前にまずここ）と
   `exbrain-vault/raw/inbox/sinria/`

軽微・純ローカル作業では省略可。ただし vault と関係するドメイン（med-evidence, Sinria, MedSpot,
EACTS research, GTM/outreach, セキュリティ監査 等）では必ず確認。

### 4層メモリモデル（保存先の判断）
- `raw/` — 一次情報・未整理 capture。Claude Code / Sinria の capture は `raw/inbox/sinria/`。
- `wiki/` — 再利用できる確定知識。`decisions/`（重要判断）, `insights/`, `digests/`（月次要約）, `memory/`。
- `workspaces/` — 実行中の作業。**primary は `workspaces/sinria/`**（handoff, daily, Taro-Inbox）。
- `configs/` — Obsidian / Claude / Sinria 設定・運用ルール。

新規ノートは乱立させず、既存の canonical ノートへの追記を優先。durable な知見は `wiki/` へ昇格、
重要判断は `wiki/decisions/` に記録。

### handoff.md 同期（絶対ルール）
**Sinria daily-log（`workspaces/sinria/daily/` または `workspaces/sinria/claude-code/daily/`）を
書き込み・更新したら、同じセッション内で必ず `workspaces/sinria/handoff.md` も最新化する。**
- handoff.md が daily-log より古いと、次担当が古い状態を掴む。
- セッション開始時に `bash configs/scripts/check-handoff-freshness.sh` で staleness を確認。
  STALE 警告が出たら、**他タスクより前に**同期する。

### Sinria 中心・legacy の扱い
- 現行の主役は **Sinria**。新規 handoff / memory / daily は `workspaces/sinria/` に書く。
- `workspaces/hermes/`・`workspaces/openclaw/`・`.openclaw` は **read-only legacy**。
  新規書き込み先にしない。OpenClaw の再起動は Taro の明示指示が必要。

### 機密（厳守）
秘密情報・APIキー・認証URL/トークン・患者個人情報（PHI/PII）・生の会話ログ本文・生の診療エビデンスを
prompt / docs / logs / cloud rows / 共有ノートに書かない。認証URL/コードは ephemeral な secret 扱い。

### 保護対象（絶対に要約・移動・削除しない）
- `日記/` — 日記エッセイ
- `daily/` — root デイリーログ
- 全 source の `raw/inbox/*/*daily-conversation-log*` — 生の日次会話ログ（= daily.log）
- `SOUL.md` / `DREAMS.md` — 理念・価値観
- `wiki/decisions/` — 要約せず原文維持

---

## Part B — 自己整理（self-reorganization）手順

Sinria が vault を「使いやすい状態」に保つための定期整理。**破壊的操作は行わず、すべて git で可逆**に。
実行前に Taro の承認、または本スキル経由の明示依頼があること。各ステップは独立コミットにする。

### 不変条件（ハードルール）
- 上記「保護対象」を要約・移動・削除しない。
- **削除ではなく `archive/` への退避**を基本にする（原本温存）。
- 着手前に **スナップショット commit ＋ ロールバック用タグ**を打つ。`git reset --hard <tag>` で完全復元可能。
- Obsidian リンク切れを作らない。ファイル移動前に被リンク密度を測る（下記）。
- 機密を新規に持ち込まない。

### 手順
**Step 0 — スナップショット**
```bash
cd ~/exbrain-vault && git add -A \
  && git commit -q -m "snapshot: pre-reorg baseline (preserve WIP)" \
  && git tag -f pre-vault-reorg-$(date +%Y-%m-%d)
```

**Step 1 — handoff 同期**（Part A のルール。STALE なら最優先で解消）

**Step 2 — 重複解消・ゴミ除去（無損失）**
- レガシー root（`insights/` 等）が 4層（`wiki/`）に byte-identical に含まれるかを diff で確認してから統合:
  ```bash
  while read -r f; do b=$(basename "$f"); \
    [ -f "wiki/insights/$b" ] && diff -q "$f" "wiki/insights/$b" >/dev/null || echo "KEEP: $b"; \
  done < <(find insights -maxdepth 1 -type f)   # 出力が空 = 完全重複 → 元を削除して安全
  ```
- ゴミ除去: 空ディレクトリ、0バイト/二重拡張子（`*.md.md`）、Obsidian の stray（`無題の*`）。

**Step 3 — ナビ点検（Sinria 中心）**
`HOME.md` `00-今日見る.md` `01-どこに何があるか.md` `docs/START-HERE.md` `docs/VAULT-INDEX.md` `README.md`
が Sinria（handoff/daily/decisions/digests）を先頭に置き、Hermes/OpenClaw を legacy 節へ降格し、
dead link（削除済みノート参照）が無いことを確認。

**Step 4 — raw ダイジェスト化＋アーカイブ**
- ノイズと知見を分離する: `Stop`/`SubagentStop`/`PreCompact`/`<date>-<hash>` 命名の**セッション自動ダンプ**は
  ノイズ（handoff/daily-conversation-log と冗長）→ まとめて archive、件数メモのみ。
  `<date>-<slug>` の **named 知見**だけをテーマ別に `wiki/digests/<source>/YYYY-MM.md` に集約。
- 被リンク密度を確認（0 なら移動安全）:
  ```bash
  grep -rl '\[\[raw/inbox' --include='*.md' . | grep -v .git | wc -l
  grep -rl '](raw/inbox' --include='*.md' . | grep -v .git | wc -l
  ```
- digest のリンクは `[[basename|Title]]`（ファイル名解決なので移動後も有効）。
- 現行月より前の **保護外** 原本を退避（保護 log は除外）:
  ```bash
  mkdir -p archive/raw-inbox/<source>/YYYY-MM
  find raw/inbox/<source> -maxdepth 1 -name 'YYYY-MM-*.md' \
    -not -name '*daily-conversation-log*' -exec mv {} archive/raw-inbox/<source>/YYYY-MM/ \;
  ```
- 現行月と全 `*daily-conversation-log*` は `raw/inbox/` に据え置く。

**Step 5 — 検証（保護対象の無傷）**
```bash
find archive -name '*daily-conversation-log*' | wc -l            # 0 であるべき
git diff --stat <tag> HEAD -- '日記/' 'daily/' SOUL.md DREAMS.md 'wiki/decisions/'   # 0 行であるべき
# ファイル保存則: 各 source で (archive済 + raw残) == 着手前件数
```

**Step 6 — コミット＆レポート**
各 Part を独立コミット。何を統合/退避/書き換えたか、保護対象が無傷である証跡、ロールバックタグを報告。

### 過去実績
- 初回の完全な再編記録: `exbrain-vault/docs/plans/2026-07-04-vault-reorg-sinria-centered.md`
  （tag `pre-vault-reorg-2026-07-04`）。手順・検証コマンドの実例として参照可。
