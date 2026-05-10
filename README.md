# My Selling Bot VVIP

Telegram bot for category-wise digital content selling with UPI/XWallet payment support and automatic file delivery.

## User Flow

1. Start bot with `/start`
2. Complete the existing verification flow
3. Open categories with `Categories` or `/categories`
4. Select a category
5. Select a subcategory/content pack
6. Review total files, full price, notes, and terms
7. Tap `Accept`
8. Choose `Pay Full` or `Pay Partial`
9. Pay through the existing payment flow
10. After successful payment verification, the bot automatically sends the saved files

## Full Payment

`Pay Full` creates an order for all files in the selected subcategory and uses the existing payment flow.

## Partial Payment

`Pay Partial` asks the user for a file range, for example:

```text
1-5
```

The bot calculates the price from the full price and total file count, then creates an order only for the selected files.

## Admin Upload Flow

Use `/addcontent` to add sellable content:

```text
/addcontent
Type existing or new category name
Type subcategory/content name
Type full price
Type notes
Type terms and conditions
Upload files one by one
/done
```

The bot creates one full-access plan for the subcategory and stores the uploaded Telegram file messages for auto-delivery.

## Admin Commands

- `/admin` - open admin panel
- `/stats` - overall users, revenue, orders, and premium count
- `/broadcast` - send any copied Telegram message to all active users
- `/admins` - open admin management
- `/addadmin user_id role` - add admin manually
- `/addpremium user_id days` - grant premium manually; omit days for lifetime

## Run

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

Before running, edit `bot\.env` and set `BOT_TOKEN`, `OWNER_ID`, `UPI_ID`, `UPI_NAME`, `SUPPORT_USERNAME`, and database settings.
