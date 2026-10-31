# Luna — Total Cost Tracker (SEK)

> **Last reviewed:** 2026-10-31 · **Currency:** Swedish kronor (SEK) · **Project:** [Luna AI Robot](https://github.com/darshanadinushal/ai-robot) · **Roadmap:** [Issue #1](https://github.com/darshanadinushal/ai-robot/issues/1)

**Note:** Luna runs on a Raspberry Pi 5, but this project's receipts and actual prices have **not** been provided. `TBD` means **unknown, not zero**. Do not treat unrecorded costs as free or purchases as confirmed.

## Cost summary

| Metric | Total (SEK) | How to update |
| --- | ---: | --- |
| **Actual spending to date** | **TBD** | Sum all confirmed paid amounts in the purchase log, including tax and shipping |
| **Estimated remaining purchases** | **TBD** | Sum only items still to be purchased; exclude alternative options |
| **Expected one-time total** | **TBD** | Actual spending + estimated remaining purchases |
| **Recurring monthly costs** | **TBD** | API and any other paid services |
| **Budget ceiling** | **TBD** | User's overall project budget |
| **Expected budget remaining** | **TBD** | Budget ceiling − expected one-time total |

The GitHub Wiki displays Markdown tables but does not recalculate totals automatically. Refresh these values manually after each purchase. Do not double-count items in both spent and planned categories.

## Equipment in use or already owned (actual prices not recorded)

| Item | Qty | Known status | Actual cost (SEK) | Notes |
| --- | ---: | --- | ---: | --- |
| Raspberry Pi 5 | 1 | In use | TBD | Runs Luna |
| Touchscreen | 1 | In use; model to confirm | TBD | Repository README specifies the official 7-inch Touch Display 2; confirm physical device |
| Current microphone | 1 | In use | TBD | Mic detected by setup verification; model not recorded |
| Current speaker / audio output | 1 | In use | TBD | Speaker detected; model not recorded |
| Existing power bank(s) | TBD | Owned; suitability not confirmed | TBD | Verify Pi load capacity and electrical protection |
| Existing cables / adapters / accessories | TBD | Partly in use | TBD | Add receipts for any purchased parts |

**Owned/in use does not automatically mean bought for Luna.** Record the actual price if known and distinguish reused hardware from project purchases.

## Planned purchases (not confirmed as bought)

| Category | Part | Qty | Status | Estimated total (SEK) | Reference |
| --- | --- | ---: | --- | ---: | --- |
| Voice | ReSpeaker XVF3800 four-mic array | 1 | Planned | TBD | Check exact board/connection |
| Voice | Compatible 4 Ω / 5 W speech speaker | 1 | Planned | TBD | Check amplifier/connector compatibility |
| Music | Detachable music speaker (JBL Flip 6 **or** Soundcore Motion+ or other) | 1 | Option | TBD | Choose one, don't sum alternatives; [audio tests #21](https://github.com/darshanadinushal/ai-robot/issues/21) |
| Mobility | Encoder DC motors | 4 | Planned | TBD | [Drivetrain design #22](https://github.com/darshanadinushal/ai-robot/issues/22) |
| Mobility | Mecanum wheels | 4 | Planned | TBD | Match wheel, motor and shaft |
| Mobility | Motor drivers | TBD | Planned | TBD | Ratings/quantity await circuit design |
| Control | ESP32 motor controller | 1 | Planned | TBD | [Controller #25](https://github.com/darshanadinushal/ai-robot/issues/25) |
| Power | Protected rechargeable battery pack | 1 | Planned | TBD | Capacity and chemistry not final |
| Power | BMS/charger and charging connector | TBD | Planned | TBD | Depends on selected pack |
| Power | Pi power regulator / DC-DC converters | TBD | Planned | TBD | Size for real current peaks |
| Safety | Physical emergency stop, fuses and main disconnect | 1 set | Planned | TBD | [Power safety #26](https://github.com/darshanadinushal/ai-robot/issues/26) |
| Power/UI | Battery state-of-charge / current monitor | 1 | Planned | TBD | For battery % and low-battery alerts |
| Vision | Camera module and cable | 1 | Planned | TBD | [Camera #31](https://github.com/darshanadinushal/ai-robot/issues/31) |
| Sensors | Distance/proximity sensors | TBD | Planned | TBD | [Independent stop #32](https://github.com/darshanadinushal/ai-robot/issues/32) |
| Chassis | 3D-print material / printing service | TBD | Planned | TBD | [Chassis #23](https://github.com/darshanadinushal/ai-robot/issues/23) |
| Assembly | Bearings, brackets, fasteners, connectors and wire | 1 set | Planned | TBD | Include shipping and spares |

The final hardware bill of materials, electrical current ratings and <5 kg mass target remain subject to validation in [Issue #22](https://github.com/darshanadinushal/ai-robot/issues/22).

## Actual purchases and expenses — source of truth

Enter **one row per receipt or payment**, including date, quantity, real amount charged in SEK, store and a public product link (optional). **Do not publish private receipts, order numbers or payment details** in this public repository.

| Date | Item | Qty | Shop | Item cost (SEK) | Extra delivery/VAT (SEK) | **Total paid (SEK)** | Note |
| --- | --- | ---: | --- | ---: | ---: | ---: | --- |
| YYYY-MM-DD | *First confirmed purchase* | 1 | — | TBD | TBD | **TBD** | Awaiting receipt |

**Total verified payments: TBD SEK.** If VAT is already in the item price, don't add it again. If splitting a receipt across multiple items, allocate shipping once. Purchased items must not remain in the *remaining-to-buy* subtotal.

## Ongoing operating costs

| Service | Status | Cost/month (SEK) | Accounting note |
| --- | --- | ---: | --- |
| OpenAI API for Luna | In use, invoice amount unconfirmed | TBD | Different billing from ChatGPT; record actual SEK invoice cost |
| Online transcription / web searches | Optional | TBD | If included in the OpenAI invoice, don't double count |
| Hosting / remote access | Not confirmed | TBD | Enter 0 only if verifiably free |
| Other services | Not confirmed | TBD | Add only Luna-related payments |

For a projected first year: **one-time total + 12 × average monthly operating cost**. For a precise annual total, sum actual invoices instead of multiplying an estimate.

## Update checklist

1. **Before purchase:** pick a verified compatible item; record link, quantity, estimate and shipping in SEK.
2. **After purchase:** add actual SEK paid and date to the purchase log; mark the planned item purchased and remove it from the remaining-purchases subtotal.
3. **After each edit:** refresh spent, remaining, expected total and budget balance in the summary.
4. **For planned hardware:** keep the hardware [BOM issue #22](https://github.com/darshanadinushal/ai-robot/issues/22), [procurement issue #24](https://github.com/darshanadinushal/ai-robot/issues/24), and [battery safety issue #26](https://github.com/darshanadinushal/ai-robot/issues/26) aligned with the cost tracker.
5. **Monthly:** reconcile the expense log and recurring costs with actual transactions. Keep alternatives and unpurchased optional accessories out of totals.

## Change history

| Date | Change |
| --- | --- |
| 2026-10-31 | Initial tracker set up; no confirmed item prices yet. |

*This is an evolving budget, not an approved electrical bill of materials.*