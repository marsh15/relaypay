#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
output_dir="$repo_root/output/release-video"
playwright_dir="$output_dir/playwright"
proof_mp4="$output_dir/proof.mp4"
title_mp4="$output_dir/title.mp4"
end_mp4="$output_dir/end.mp4"
concat_list="$output_dir/concat.txt"
final_mp4="$output_dir/relaypay-v0.1.0-proof.mp4"

command -v ffmpeg >/dev/null || { echo "ffmpeg is required" >&2; exit 1; }
mkdir -p "$output_dir"
rm -rf "$playwright_dir"

cd "$repo_root/apps/console"
npx playwright test --config playwright.video.config.ts

recording="$(find "$playwright_dir" -name video.webm -type f -print -quit)"
test -n "$recording" || { echo "Playwright did not produce video.webm" >&2; exit 1; }

ffmpeg -y -f lavfi -i color=c=0x0b1020:s=1440x900:d=4:r=30 \
  -vf "drawtext=text='RelayPay v0.1.0':fontcolor=white:fontsize=68:x=(w-text_w)/2:y=350,drawtext=text='Exactly-once recovery proof':fontcolor=0x8ee3cf:fontsize=36:x=(w-text_w)/2:y=450" \
  -c:v libx264 -pix_fmt yuv420p "$title_mp4"
ffmpeg -y -i "$recording" -vf scale=1440:900,fps=30 -an -c:v libx264 -pix_fmt yuv420p "$proof_mp4"
ffmpeg -y -f lavfi -i color=c=0x0b1020:s=1440x900:d=5:r=30 \
  -vf "drawtext=text='Synthetic INR data only':fontcolor=white:fontsize=54:x=(w-text_w)/2:y=360,drawtext=text='Not for real financial or personal data':fontcolor=0x8ee3cf:fontsize=32:x=(w-text_w)/2:y=450" \
  -c:v libx264 -pix_fmt yuv420p "$end_mp4"

printf "file '%s'\nfile '%s'\nfile '%s'\n" "$title_mp4" "$proof_mp4" "$end_mp4" > "$concat_list"
ffmpeg -y -f concat -safe 0 -i "$concat_list" \
  -vf "subtitles='$repo_root/scripts/video/captions.srt':force_style='FontSize=22,Outline=2,MarginV=36'" \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart "$final_mp4"

printf 'Created %s\n' "$final_mp4"
