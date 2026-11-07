#!/bin/bash
# ذخیره‌ی state در شاخه‌ی state (هم هنگام اجرا هر ۶۰ ثانیه، هم در پایان)
cd state || exit 0
git config user.name "balebot-state"
git config user.email "balebot-state@users.noreply.github.com"
git add -A
if ! git diff --cached --quiet; then
  git commit --amend -q -m "state"
  git push -q --force origin HEAD:state
fi
