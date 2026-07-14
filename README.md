# Trading Bot (paper-first)

## Current $200 risk profile

New BUY orders use AI Confidence sizing: 70-79% uses 5% of the portfolio,
80-89% uses 10%, 90-95% uses 15%, and above 95% uses 20%. Every tier is
still capped by `MAX_ORDER_NOTIONAL=50`, and quantities are rounded down to
whole shares so the cap is never exceeded.

The active profile uses a $200 portfolio, at most $50 per order, at most three
concurrent positions/$150 aggregate exposure, a 2% initial stop loss, a 10%
take profit, a 1.5% trailing stop, a 2% daily loss limit (currently $4), and at
most ten BUY orders per day.
Account DailyPnL is monitored during the wait loop; when the limit is reached
during market hours, protective orders are cancelled with confirmation and
the remaining configured long positions are flattened before the bot stops.
IBKR TWS stock orders in this bot are whole-share orders; if one share costs
more than the confidence budget, no order is sent and LINE receives one
`ORDER SIZE BLOCKED` alert per symbol/budget/day.

บอทวิเคราะห์หุ้นเป็นรอบ ส่งคำสั่งซื้อ และแจ้ง LINE Messaging API โดยเก็บรายการซื้อขายและสถานะใน Microsoft SQL Server ซึ่งจัดการผ่าน SSMS ได้

## เริ่มต้น

1. ติดตั้ง Python 3.12 แล้วรัน `python -m venv .venv`
2. เปิด venv และรัน `pip install -r requirements.txt`
3. เปิด `scripts/create-sqlserver.sql` ใน SSMS แล้ว Execute
4. คัดลอก `.env.example` เป็น `.env` แล้วตั้ง `DATABASE_URL` รูปแบบเดียวกับโปรเจกต์ Invoice
5. รัน `python -m trading_bot.main`

หรือรันตลอดด้วย `docker compose up -d --build` และดู log ด้วย `docker compose logs -f` การรันใน Linux container ควรใช้ SQL Server username/password เพราะ Windows integrated authentication ใช้ไม่ได้โดยอัตโนมัติใน container.

## Interactive Brokers

รองรับการส่งคำสั่งหุ้นผ่าน TWS หรือ IB Gateway โดยใช้ TWS API:

1. ล็อกอินบัญชี Paper ใน TWS/IB Gateway
2. เปิด `Global Configuration > API > Settings > Enable ActiveX and Socket Clients`
3. ตั้ง `BROKER=ibkr`, `IBKR_PORT=7497` และ `IBKR_PAPER=true`
4. รันทดสอบการเชื่อมต่อโดยคง `IBKR_READ_ONLY=true` ก่อน
5. เมื่อยืนยันบัญชี, position และ symbol ถูกต้องแล้ว จึงตั้ง `IBKR_READ_ONLY=false` เพื่ออนุญาตคำสั่ง Paper

ตรวจการเชื่อมต่อและรายการ Position แบบ read-only ได้ด้วย `python -m trading_bot.check_ibkr` คำสั่งนี้จะไม่ส่งออเดอร์

พอร์ตมาตรฐานคือ TWS Paper `7497`, TWS Live `7496`, Gateway Paper `4002` และ Gateway Live `4001` ระบบแบบผู้ใช้คนเดียวนี้บังคับ `IBKR_HOST=127.0.0.1` เท่านั้น จึงไม่เปิดรับ TWS API จากเครื่องอื่นหรือ Docker network

`IBKR_ACCOUNT` เว้นว่างได้เมื่อ login จัดการเพียงบัญชีเดียว หากมีหลายบัญชีต้องระบุ account ID ให้ชัดเจน ระบบจะปฏิเสธการเริ่มทำงานหากตั้ง Paper/Live ไม่ตรงกับพอร์ตมาตรฐาน หรือระบุชื่อ `BROKER` ที่ไม่รองรับ

## Security

ตั้ง `ALLOWED_OS_USER` เพื่อจำกัดให้บอทรันได้เฉพาะ Windows account ที่กำหนด ระบบใช้ OS process token ตรวจชื่อผู้ใช้และใช้ file lock ป้องกันการเปิดบอทซ้อนกันมากกว่าหนึ่ง instance ควรจำกัด ACL ของโฟลเดอร์โปรเจกต์และ `.env` ให้เฉพาะเจ้าของเครื่องกับ `SYSTEM` และไม่ควรสร้าง inbound firewall exception ให้ `tws.exe` เพราะการเชื่อมต่อบอทใช้ localhost เท่านั้น

หลังอัปเดตหรือติดตั้ง TWS ใหม่ ควรตรวจ Windows Firewall อีกครั้งว่าไม่มี inbound allow rule สำหรับ `tws.exe` จากทุก IP และควรคง `KILL_SWITCH=true`, `IBKR_READ_ONLY=true` ระหว่างตรวจระบบทุกครั้ง

## Start / Stop

รันเบื้องหลังด้วย `python -m trading_bot.control start`, ตรวจสถานะด้วย `python -m trading_bot.control status` และหยุดแบบ graceful ด้วย `python -m trading_bot.control stop` โดยดู log ได้ที่ `trading-bot.log` การหยุดโปรแกรมจะไม่ขายหรือปิด Position ที่เปิดอยู่ ต้องตรวจและจัดการ Position ใน TWS แยกต่างหาก

## LINE

สร้าง LINE Official Account และ Messaging API channel, ออก Channel access token, เพิ่ม OA เป็นเพื่อน/เข้ากลุ่ม แล้วใส่ token กับ userId/groupId ใน `LINE_CHANNEL_ACCESS_TOKEN` และ `LINE_TARGET_ID` ตามลำดับ

### LINE remote control

ตั้ง `LINE_CHANNEL_SECRET` จากหน้า Basic settings และตั้ง `LINE_CONTROL_USER_ID` เป็น user ID ส่วนตัวที่ขึ้นต้นด้วย `U` Controller รับเฉพาะแชตส่วนตัวจาก ID นี้ ตรวจ HMAC-SHA256 ของ raw webhook body ทุกครั้ง และ bind เฉพาะ `127.0.0.1`

เริ่ม controller ด้วย `python -m trading_bot.control controller-start`, ตรวจด้วย `python -m trading_bot.control controller-status` และหยุดด้วย `python -m trading_bot.control controller-stop` Controller ต้องรันตลอดเพื่อให้คำสั่ง LINE `เริ่ม`, `หยุด` และ `สถานะ` ทำงาน แม้ตัว TradingBot จะหยุดอยู่

LINE กำหนดให้ Webhook URL เป็น public HTTPS จึงต้องใช้ HTTPS tunnel/reverse proxy ที่ชี้มายัง `http://127.0.0.1:8080/webhook` ห้ามเปิดพอร์ต 8080 บน router โดยตรง หลังตั้ง URL ใน LINE Developers Console ให้เปิด Use webhook และกด Verify

## เกณฑ์ 70%

ระบบจับคู่สถานะ EMA/RSI ปัจจุบันกับอดีต แล้ววัดว่า Take Profit 10% ถึงก่อน Stop Loss 2% ภายใน `SIGNAL_HORIZON_BARS` หรือไม่ หากราคาแตะทั้งสองระดับในแท่งเดียวกันจะนับ Stop Loss ก่อนเพื่อไม่ให้ผลทดสอบดีเกินจริง นอกจากนี้ Volume ล่าสุดต้องไม่น้อยกว่าค่าเฉลี่ย 20 แท่งและ ATR ต้องอยู่ในช่วงที่กำหนด Confidence ใช้ขอบล่าง Wilson 90% ก่อนส่ง BUY ระบบเรียก IBKR What-If และเผื่อ Commission เพิ่ม 10% แล้วหัก Commission, Exchange Fee, FX Cost และภาษีโดยประมาณ ระบบจะซื้อเฉพาะเมื่อ Expected Net Profit มากกว่า `MIN_NET_PROFIT_COST_MULTIPLE` เท่าของ Trading Cost เท่านั้น ผลย้อนหลังไม่รับประกันผลอนาคต

อัตราภาษีเลือกได้เฉพาะ `TAX_RATE=0`, `0.10`, `0.15` หรือ `0.20` ค่า Exchange Fee และ FX Cost เป็นค่าประมาณแบบ round-trip ต่อมูลค่าเงินลงทุน ส่วนคอมมิชชันจริงหลัง fill มาจาก IBKR และระบบจะคำนวณ Net Profit ใหม่เมื่อรายงานค่าคอมมิชชันมาถึง
ก่อน Live ต้องยืนยันด้วย `LIVE_TAX_CONFIRM=I_CONFIRMED_TAX_RATE` และ `LIVE_COST_MODEL_CONFIRM=I_VERIFIED_TRADING_COSTS` หลังตรวจอัตราภาษีของผู้ใช้และต้นทุนจริงจาก IBKR แล้วเท่านั้น

`PAPER_TEST_SYMBOL` ใช้เฉพาะการทดสอบ Paper round-trip หนึ่งหุ้น และไม่เปลี่ยนรายการ `SYMBOLS` ที่กลยุทธ์ติดตาม การทดสอบนี้ตั้งใจซื้อและขายทันทีเพื่อตรวจ Fill, native bracket, SQL และ LINE เท่านั้น จึงอาจแสดงผลขาดทุนจำลองจากค่าคอมมิชชัน
Execution จากการทดสอบนี้ถูกบันทึกเป็น `connectivity_test` และจะไม่ถูกนับรวมในกำไร, Win Rate, จำนวนไม้ หรือจำนวนวันของ Paper track record สำหรับปลดล็อก Live

## ใช้เงินจริง

รองรับ Alpaca สำหรับหุ้นสหรัฐ: ตั้ง `BROKER=alpaca`, กรอก keys และคง `ALPACA_PAPER=true` ระหว่างทดสอบ การเปิด live ต้องตั้ง `ALPACA_PAPER=false` ด้วยตนเอง หุ้นไทยต้องเพิ่ม adapter ของโบรกเกอร์ที่ผู้ใช้มีบัญชีและ API อย่างถูกต้อง

ข้อควรทราบ: ระบบปิดสถานะเมื่อราคาที่ตรวจพบแตะ stop-loss/take-profit แต่เวอร์ชันนี้ยังไม่เปิด short และ stop ไม่ได้วางค้างไว้ที่ exchange จึงอาจเกิด slippage ระหว่างรอบตรวจ การใช้งานเงินจริงควรเพิ่ม bracket orders, reconciliation ของ order fills, market-hours calendar และ monitoring/alerting.
## Preflight

Run a non-trading paper readiness check with:

`python -m trading_bot.preflight --mode paper --send-line-test`

Before checking Paper order permission, disable Read-Only API in TWS, keep
`KILL_SWITCH=true`, set `IBKR_READ_ONLY=false`, and run:

`python -m trading_bot.preflight --mode paper --check-order-permission`

The permission check uses an IBKR What-If order and never transmits it. Live
startup additionally requires a live port/account plus the exact setting
`LIVE_TRADING_CONFIRM=I_UNDERSTAND_LIVE_ORDERS`. `MAX_ORDER_NOTIONAL` provides
an absolute per-order cap in addition to the percentage risk limits.

## Live safety gates

Live mode requires `python -m trading_bot.preflight --mode live` to pass with a live account/port, market data type 1, a fresh quote, no existing positions or open orders, and an IBKR What-If validation.

New entries are blocked by `MAX_ORDER_NOTIONAL`, `MAX_TOTAL_EXPOSURE`, `MAX_DAILY_LOSS`, `MAX_ORDERS_PER_DAY`, `MAX_CONSECUTIVE_LOSSES`, and `MAX_CONSECUTIVE_CYCLE_ERRORS`. On restart, an existing position must have matching Stop Loss and Take Profit orders; a failed repair stops the bot.

LINE command `เริ่ม` starts Paper mode only. Live mode requires the exact command `เริ่ม live ยืนยัน`. The `หยุด` command stops the program, while protective orders already accepted by IBKR remain active for the open position.

Live preflight expires after `LIVE_PREFLIGHT_VALID_HOURS` and is invalidated by
strategy, sizing, risk, market-data, or protection-setting changes. Before a
LINE Live start, run `python -m trading_bot.control arm-live` locally; the arm
token expires after ten minutes and can be used once. The controller monitors
the bot heartbeat and sends a CRITICAL LINE alert when an expected process dies
or becomes stale.

Run `scripts/disable-tws-public-inbound.ps1` from an Administrator PowerShell
once to disable Windows Public-profile inbound rules created for `tws.exe`.
The bot requires `IBKR_HOST` to be loopback-only, so disabling those rules does
not prevent local TWS API access.
