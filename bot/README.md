# My Selling Bot VVIP

Category-wise Telegram content selling bot with manual UPI/XWallet payment verification and automatic file delivery.

## Features

- Category-first browsing flow
- Subcategories/content packs inside each category
- Full pack purchase
- Partial file-range purchase
- Existing manual UPI screenshot and XWallet payment flow
- Admin approval/rejection where required
- Automatic file/message delivery after successful payment
- Admin product, content, settings, broadcast, and role management
- MongoDB database

## Local Setup

Use Python 3.10 or 3.11. This device currently has Python 3.10 available.

```bash
cd "C:\Users\anshu\OneDrive\Documents\my selling bot vvip"
py -3.10 -m venv venv
venv\Scripts\activate
pip install -r bot\requirements.txt
copy bot\.env.example bot\.env
notepad bot\.env
cd bot
python main.py
```

Required `.env` values:

- `BOT_TOKEN`
- `OWNER_ID`
- `MONGODB_URL`
- `MONGODB_DB_NAME`
- `UPI_ID`
- `UPI_NAME`
- `STORE_NAME`
- `SUPPORT_USERNAME`

## User Flow

1. `/start`
2. Complete existing verification
3. `Categories` or `/categories`
4. Select category
5. Select subcategory/content pack
6. Review total files, full price, notes, and terms
7. `Accept`
8. Choose `Pay Full` or `Pay Partial`
9. Pay through the existing payment flow
10. Bot automatically sends the purchased files after payment success

## Admin Upload Flow

Use `/addcontent`:

```text
/addcontent
Category name
Subcategory/content name
Full price
Notes
Terms and conditions
Upload files one by one
/done
```

The bot shows an overview after `/done` with category, subcategory, total files, and full price.

The uploaded files are saved as Telegram message references. After payment approval or automatic payment success, the bot copies only the purchased files to the buyer.

## Admin Commands

- `/admin` - open admin panel
- `/stats` - show overall users, orders, revenue, and premium users
- `/broadcast` - start broadcast flow and copy the next message to all active users after confirmation
- `/admins` - list admins and manage roles
- `/addadmin user_id role` - add an admin directly
- `/addpremium user_id days` - manually grant premium access; omit days for lifetime

Allowed `/addadmin` roles:

```text
super_admin
product_admin
payment_admin
order_admin
```
