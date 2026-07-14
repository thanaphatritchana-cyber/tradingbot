# Trading Bot (paper-first)

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

พอร์ตมาตรฐานคือ TWS Paper `7497`, TWS Live `7496`, Gateway Paper `4002` และ Gateway Live `4001` หากบอทรันใน Docker แต่ TWS รันบนเครื่อง host ให้ใช้ `IBKR_HOST=host.docker.internal` และเพิ่ม IP ที่จำเป็นใน Trusted IPs ของ TWS

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

ระบบจับคู่สถานะ EMA/RSI ปัจจุบันกับอดีต วัดว่าราคาหลัง 5 แท่งสูงขึ้นกี่ครั้ง และใช้ขอบล่าง Wilson ที่ระดับความเชื่อมั่น 90% เป็นค่าประเมินแบบอนุรักษนิยม ต้องมีตัวอย่างอย่างน้อย `MIN_SIGNAL_SAMPLES` และค่าขอบล่างไม่น้อยกว่า `MIN_WIN_PROBABILITY` จึงซื้อ ผลย้อนหลังไม่รับประกันผลอนาคต

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
