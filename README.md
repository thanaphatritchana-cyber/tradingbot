# Trading Bot (paper-first)

บอทวิเคราะห์หุ้นเป็นรอบ ส่งคำสั่งซื้อ และแจ้ง LINE Messaging API โดยเก็บรายการซื้อขายและสถานะใน Microsoft SQL Server ซึ่งจัดการผ่าน SSMS ได้

## เริ่มต้น

1. ติดตั้ง Python 3.12 แล้วรัน `python -m venv .venv`
2. เปิด venv และรัน `pip install -r requirements.txt`
3. เปิด `scripts/create-sqlserver.sql` ใน SSMS แล้ว Execute
4. คัดลอก `.env.example` เป็น `.env` แล้วตั้ง `DATABASE_URL` รูปแบบเดียวกับโปรเจกต์ Invoice
5. รัน `python -m trading_bot.main`

หรือรันตลอดด้วย `docker compose up -d --build` และดู log ด้วย `docker compose logs -f` การรันใน Linux container ควรใช้ SQL Server username/password เพราะ Windows integrated authentication ใช้ไม่ได้โดยอัตโนมัติใน container.

## LINE

สร้าง LINE Official Account และ Messaging API channel, ออก Channel access token, เพิ่ม OA เป็นเพื่อน/เข้ากลุ่ม แล้วใส่ token กับ userId/groupId ใน `LINE_CHANNEL_ACCESS_TOKEN` และ `LINE_TARGET_ID` ตามลำดับ

## เกณฑ์ 70%

ระบบจับคู่สถานะ EMA/RSI ปัจจุบันกับอดีต วัดว่าราคาหลัง 5 แท่งสูงขึ้นกี่ครั้ง และใช้ขอบล่าง Wilson ที่ระดับความเชื่อมั่น 90% เป็นค่าประเมินแบบอนุรักษนิยม ต้องมีตัวอย่างอย่างน้อย `MIN_SIGNAL_SAMPLES` และค่าขอบล่างไม่น้อยกว่า `MIN_WIN_PROBABILITY` จึงซื้อ ผลย้อนหลังไม่รับประกันผลอนาคต

## ใช้เงินจริง

รองรับ Alpaca สำหรับหุ้นสหรัฐ: ตั้ง `BROKER=alpaca`, กรอก keys และคง `ALPACA_PAPER=true` ระหว่างทดสอบ การเปิด live ต้องตั้ง `ALPACA_PAPER=false` ด้วยตนเอง หุ้นไทยต้องเพิ่ม adapter ของโบรกเกอร์ที่ผู้ใช้มีบัญชีและ API อย่างถูกต้อง

ข้อควรทราบ: ระบบปิดสถานะเมื่อราคาที่ตรวจพบแตะ stop-loss/take-profit แต่เวอร์ชันนี้ยังไม่เปิด short และ stop ไม่ได้วางค้างไว้ที่ exchange จึงอาจเกิด slippage ระหว่างรอบตรวจ การใช้งานเงินจริงควรเพิ่ม bracket orders, reconciliation ของ order fills, market-hours calendar และ monitoring/alerting.
