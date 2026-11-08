# Provenance

Sanitized source export used for this candidate:

`archive.tar_c457.gz` SHA256 `0081aefa3b8ac67cc51dada790e7839afdaf8f54f7858c35146e190e8a40e9b0`

File hashes below are SHA256 of the bytes in that export. This package does not vendor those files.

| Export path | SHA256 | Role in this review |
| --- | --- | --- |
| `kalshi-demo/kalshi_demo.py` | `8088d86794d197bee4ac7da7f8434d407631093c1bbfd27f14b5150a6e031f27` | DEMO host rule, UUID-at-POST, IOC then a second UUID for the remainder, `sweep_resting` inference, shard transfer |
| `kalshi-demo/runner.py` | `b7945f0e25370061f2555b4d9614f3152e64c718fea080eda5b1ae7e888ed96f` | `place_order` at 162-167 and 211-216, log after the call returns |
| `kalshi-demo/me_kalshi.py` | `d39a27955c66f31b437d0c8919969259306fcd2db360f6f56835c270b9ae91d0` | `place_order` at 344-351 |
| `kalshi-demo/live_runner.py` | `73368f4d0b98bba856cc78bccbed98806e7174a20066e4f057a0725b3cccb10f` | `place_order` at 111-115; also calls `sweep_resting` |
| `kalshi-demo/kalshi_nba_pacer.py` | `32221f11f71b9afa027ae98ad9f9af407c49554950cffc17ddea926f729b4a52` | `place_order` at 196-200; also calls `sweep_resting` |
| `kalshi-demo/marv_cbb_kalshi.py` | `600cfd1dbfb6fe008346814c927d624937a7cab7134898d9419d059749eb5b8e` | `place_order` at 57-61 |
| `marv-feed/live_props/live_props.py` | `cd8f993e091fbd0a8168cd55a883507cdffae726744e84266afd07c22906032c` | `place_order` at 273-278; paper fill is a separate local call |
| `kalshi-demo/side_guard.py` | `f8e2fd1cd332f87681f19a92daee83b76857b615586c3c2fe6c0943401d7dfc8` | Out of scope. Rank 2 identity |
| `polymarket-paper/pm_client.py` | `c5a19173ad518d6e7611471e34f13796004019c9cd74b99d0ad2b7ed542bd8b0` | Public GETs only. No order method |
| `polymarket-paper/pm_fill.py` | `d9ec5d4e51ee903d8a413e5e9c5a67817febe76a049e51bcd4f7e5d82f8cdafa` | Local insert. Fee constant is not recomputed here |
| `polymarket-paper/runner.py` | `bdc4dfe5b103c2acef8f04e01559927fb03cd7ef0ffb95772b5b58f1825fb3dc` | Local paper fill log |
| `marv-bot/deploy/marv-health.sh` | `5b881154c6fbc6efeb4df4339444277d2cd1bae0adb1bdc8635ae4c66d739b4a` | Read-only comparison with PR 7. Not edited |
| `marv-feed/marv_lane_pause.py` | `8929f00007afcf00d4d9cce32b4c8f3cff801af7d48472ece027378586774cf4` | Read-only comparison with PR 7. Not edited |

The DEMO allowlist in `order_safety/host.py` matches `kalshi_demo.py` lines 8-40: `demo-api.kalshi.co` and `external-api.demo.kalshi.co`, and the host string must contain `demo`.
