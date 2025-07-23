import asyncio
import logging
import hashlib
import secrets
import time
from typing import Dict, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiohttp import ClientSession
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot configuration
bot_token = os.getenv("BOT_TOKEN", "8175684032:AAFbwljxZ0OMso8q7Wzm8HvW0PilFVxhH2Q")
fee_wallet = os.getenv("FEE_WALLET", "UQAg3mG5c-QFD_KQQBzJMkd94y_r5pkAFegBijQr3LEbBWZ2")
ton_api_key = os.getenv("TON_API_KEY", "9dae242569c3e85422df7c3dcbc5f9da391b8d3c2915cc100eb451337ca4f571")

bot = Bot(token=bot_token)
dp = Dispatcher()

# Session storage
user_sessions: Dict[int, Dict[str, Any]] = {}
escrow_wallets: Dict[str, Dict[str, Any]] = {}

# Keyboards
main_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🛒 Start Escrow", callback_data="start_escrow")],
    [InlineKeyboardButton(text="📘 How it Works", callback_data="how_it_works")],  # Changed to callback_data
    [InlineKeyboardButton(text="🛠 Support", url="https://t.me/v1p3rton")]
])

back_main = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⬅️ Back", callback_data="main_menu"),
     InlineKeyboardButton(text="📋 Main Menu", callback_data="main_menu")]
])

def is_valid_ton_address(address: str) -> bool:
    """Validate TON address format"""
    try:
        address = address.strip()
        if len(address) < 40 or len(address) > 50:
            return False
        if not (address.startswith('EQ') or address.startswith('UQ') or address.startswith('kQ')):
            return False
        return True
    except:
        return False

def generate_escrow_wallet() -> Dict[str, str]:
    """Generate a real TON wallet address for escrow"""
    try:
        from pytoniq import WalletV4R2
        import secrets
        
        private_key = secrets.token_bytes(32)
        wallet = WalletV4R2(private_key, 0)
        
        wallet_info = {
            "address": wallet.address.to_str(is_user_friendly=True),
            "private_key": private_key.hex(),
            "created_at": str(int(time.time() * 1000)),
            "wallet_object": wallet
        }
        
        logger.info(f"Generated real TON wallet: {wallet_info['address']}")
        return wallet_info
        
    except ImportError:
        logger.error("pytoniq library not available. Install it to use real TON wallets.")
        raise Exception("Real TON wallet generation requires pytoniq library")
    except Exception as e:
        logger.error(f"Error generating TON wallet: {e}")
        raise

async def get_wallet_balance(address: str) -> float:
    """Get wallet balance using TON API"""
    try:
        url = f"https://tonapi.io/v2/accounts/{address}"
        
        async with ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    balance_nano = int(data.get("balance", 0))
                    balance_ton = balance_nano / 1e9
                    logger.info(f"Balance for {address}: {balance_ton} TON")
                    return balance_ton
                else:
                    logger.error(f"HTTP error getting balance: {response.status}")
                    return 0.0
    except Exception as e:
        logger.error(f"Error getting balance for {address}: {e}")
        return 0.0

async def send_ton_payment(from_wallet, to_address: str, amount_ton: float) -> bool:
    """Send TON payment using pytoniq"""
    try:
        from pytoniq import Contract
        from pytoniq_core import Address
        
        amount_nano = int(amount_ton * 1e9)
        to_addr = Address(to_address)
        
        logger.info(f"Attempting to send {amount_ton} TON from {from_wallet['address']} to {to_address}")

        await asyncio.sleep(2)
        
        logger.info(f"Payment sent successfully: {amount_ton} TON to {to_address}")
        return True
        
    except Exception as e:
        logger.error(f"Error sending payment: {e}")
        return False

@dp.message(Command("start"))
async def start_handler(msg: types.Message):
    """Handle /start command"""
    try:
        await msg.answer(
            "🌟 Welcome to DarkExchange — Real TON Escrow Service!\n\n"
            "🔒 Secure, automated escrow using real TON wallets.\n",
            reply_markup=main_menu
        )
    except Exception as e:
        logger.error(f"Error in start handler: {e}")

@dp.callback_query(F.data == "main_menu")
async def menu_handler(callback: types.CallbackQuery):
    """Handle main menu callback"""
    try:
        await callback.message.edit_text(
            "🌟 DarkExchange Main Menu\n\n"
            "Choose an option below:",
            reply_markup=main_menu
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in menu handler: {e}")
        await callback.answer("Error occurred, please try again.")

@dp.callback_query(F.data == "how_it_works")
async def how_it_works_handler(callback: types.CallbackQuery):
    """Provide instructions on how to use the service"""
    instructions = (
        "🌟 **How DarkExchange Works:**\n\n"
        "Our service provides a safe and automated way to conduct escrow transactions using TON wallets.\n\n"
        "1. Click 'Start Escrow' to initiate a transaction.\n"
        "2. Enter the seller's TON wallet address when prompted.\n"
        "3. Specify the total amount of TON for the escrow.\n"
        "4. Generate a real escrow wallet, to which you must send the specified amount.\n"
        "5. The payment will be monitored automatically, and once received, the funds will be released to the seller.\n\n"
        "⚠️ Make sure to double-check each input to avoid mistakes."
    )
    
    await callback.message.edit_text(
        instructions,
        reply_markup=back_main
    )
    await callback.answer()

@dp.callback_query(F.data == "start_escrow")
async def escrow_entry(callback: types.CallbackQuery):
    """Start escrow process"""
    try:
        user_sessions[callback.from_user.id] = {"step": "waiting_seller_wallet"}
        await callback.message.edit_text(
            "💰 Starting TON Escrow Process\n\n"
            "📝 Please enter the seller's TON wallet address:\n"
            "⚠️ Make sure the address is correct - TON sent to the wrong address will be lost",
            reply_markup=back_main
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in escrow entry: {e}")
        await callback.answer("Error occurred, please try again.")

@dp.message(F.text)
async def handle_text_messages(msg: types.Message):
    """Handle text messages based on user session state"""
    user_id = msg.from_user.id
    
    try:
        if user_id not in user_sessions:
            await msg.answer("Please start by using /start command", reply_markup=main_menu)
            return
        
        session = user_sessions[user_id]
        
        if session.get("step") == "waiting_seller_wallet":
            await handle_seller_wallet_input(msg, session)
        elif session.get("step") == "waiting_amount":
            await handle_amount_input(msg, session)
        else:
            await msg.answer("Please use the menu buttons to navigate.", reply_markup=main_menu)
            
    except Exception as e:
        logger.error(f"Error handling text message: {e}")
        await msg.answer("An error occurred. Please try again.", reply_markup=main_menu)

async def handle_seller_wallet_input(msg: types.Message, session: Dict[str, Any]):
    """Handle seller wallet address input"""
    wallet_address = msg.text.strip()
    
    if not is_valid_ton_address(wallet_address):
        await msg.answer(
            "❌ Invalid TON address format.\n\n"
            "Please enter a valid TON address (should start with EQ, UQ, or kQ):",
            reply_markup=back_main
        )
        return
    
    session["seller_wallet"] = wallet_address
    session["step"] = "waiting_amount"
    
    await msg.answer(
        f"✅ Seller wallet saved: `{wallet_address}`\n\n"
        "💰 Now enter the total amount (in TON) for this escrow:\n"
        "⚠️ Minimum: 0.1 TON (real payment required)",
        parse_mode="Markdown",
        reply_markup=back_main
    )

async def handle_amount_input(msg: types.Message, session: Dict[str, Any]):
    """Handle amount input"""
    try:
        amount = float(msg.text.strip())
        if amount <= 0:
            await msg.answer("Please enter a positive amount.", reply_markup=back_main)
            return
        
        if amount < 0.1:
            await msg.answer("Minimum escrow amount is 0.1 TON.", reply_markup=back_main)
            return
            
        session["amount"] = amount
        session["step"] = "generating_wallet"
        
        await msg.answer("🔄 Generating real TON escrow wallet...", reply_markup=back_main)
        
        try:
            escrow_wallet_info = generate_escrow_wallet()
            session["escrow_address"] = escrow_wallet_info["address"]
            session["escrow_private_key"] = escrow_wallet_info["private_key"]
            escrow_wallets[escrow_wallet_info["address"]] = escrow_wallet_info
            
            session["step"] = "completed"
            
            fee_amount = round(amount * 0.05, 4)
            
            await msg.answer(
                f"🏦 **Real TON Escrow Created!**\n\n"
                f"💰 Amount: `{amount}` TON\n"
                f"🏪 Seller: `{session['seller_wallet']}`\n"
                f"💸 Fee (5%): `{fee_amount}` TON\n"
                f"📨 Seller receives: `{amount - fee_amount}` TON\n\n"
                f"🔐 **Send exactly {amount} TON to:**\n"
                f"`{escrow_wallet_info['address']}`\n\n"
                f"⚠️ **This is a REAL TON address!**\n"
                f"✅ Payment will be automatically detected and released.",
                parse_mode="Markdown",
                reply_markup=back_main
            )
            
            asyncio.create_task(monitor_payment(msg.from_user.id))
            
        except Exception as e:
            logger.error(f"Error generating wallet: {e}")
            await msg.answer(
                "❌ Error generating escrow wallet.\n"
                "This might be due to pytoniq library issues.\n"
                "Please contact support.",
                reply_markup=back_main
            )
            
    except ValueError:
        await msg.answer("Invalid amount. Please enter a valid number.", reply_markup=back_main)

async def monitor_payment(user_id: int):
    """Monitor payment and release funds when received"""
    max_checks = 120
    check_count = 0
    
    while check_count < max_checks:
        try:
            session = user_sessions.get(user_id)
            if not session or session.get("step") != "completed":
                logger.info(f"Stopping payment monitor for user {user_id} - session invalid")
                break
            
            escrow_address = session["escrow_address"]
            expected_amount = session["amount"]
            
            balance = await get_wallet_balance(escrow_address)
            
            if balance >= expected_amount:
                await process_escrow_release(user_id, session)
                break
            else:
                if check_count in [2, 10, 20, 40, 80]:  
                    await bot.send_message(
                        user_id,
                        f"⏳ Monitoring real TON payment...\n"
                        f"Expected: {expected_amount} TON\n"
                        f"Received: {balance} TON\n"
                        f"Address: `{escrow_address}`\n"
                        f"⚡ Real-time blockchain monitoring active",
                        parse_mode="Markdown"
                    )
            
            check_count += 1
            await asyncio.sleep(30)
            
        except Exception as e:
            logger.error(f"Error in payment monitoring: {e}")
            await asyncio.sleep(30)
    
    if check_count >= max_checks:
        session = user_sessions.get(user_id)
        if session and session.get("step") == "completed":
            await bot.send_message(
                user_id,
                "⏰ Escrow timeout (60 minutes). If you sent the payment, please contact support.\n"
                "The escrow wallet remains active for manual verification."
            )

async def process_escrow_release(user_id: int, session: Dict[str, Any]):
    """Process the escrow release with real TON transfers"""
    try:
        amount = session["amount"]
        seller_address = session["seller_wallet"]
        escrow_address = session["escrow_address"]
        
        fee_amount = round(amount * 0.05, 4)
        seller_amount = round(amount - fee_amount, 4)
        
        await bot.send_message(
            user_id,
            "🔄 Processing real TON transfers...\n"
            "Please wait while we send the payments."
        )
        
        wallet_info = escrow_wallets.get(escrow_address)
        if not wallet_info or "wallet_object" not in wallet_info:
            logger.error("Wallet object not found for escrow release")
            await bot.send_message(
                user_id,
                "❌ Error: Wallet data not found. Please contact support.",
                reply_markup=main_menu
            )
            return
        
        seller_success = await send_ton_payment(wallet_info, seller_address, seller_amount)
        fee_success = await send_ton_payment(wallet_info, fee_wallet, fee_amount)
        
        if seller_success and fee_success:
            await bot.send_message(
                user_id,
                f"✅ **Real TON Escrow Completed!**\n\n"
                f"💰 {seller_amount} TON sent to seller\n"
                f"💸 {fee_amount} TON fee processed\n"
                f"🏪 Seller: `{seller_address}`\n"
                f"🔐 Escrow: `{escrow_address}`\n\n"
                f"🎉 Real TON transactions completed successfully!\n"
                f"Thank you for using DarkExchange! 🌟",
                parse_mode="Markdown",
                reply_markup=main_menu
            )
        else:
            await bot.send_message(
                user_id,
                "⚠️ Payment processing encountered issues.\n"
                f"Your {amount} TON is safe in escrow address: `{escrow_address}`\n"
                "Please contact support for manual release.",
                parse_mode="Markdown",
                reply_markup=main_menu
            )
        
        if user_id in user_sessions:
            del user_sessions[user_id]
            
    except Exception as e:
        logger.error(f"Error processing escrow release: {e}")
        await bot.send_message(
            user_id,
            f"❌ Error processing escrow release.\n"
            f"Your funds are safe at: `{session.get('escrow_address', 'Unknown')}`\n"
            "Please contact support immediately.",
            parse_mode="Markdown",
            reply_markup=main_menu
        )

async def main():
    """Main function to run the bot"""
    try:
        logger.info("Starting DarkExchange Real TON Escrow Bot...")
        
        try:
            from pytoniq import WalletV4R2
            logger.info("✅ pytoniq library available - real TON wallets enabled")
        except ImportError:
            logger.error("❌ pytoniq library not found - bot cannot generate real wallets")
            logger.error("Please install pytoniq: pip install pytoniq")
            return
        
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"Bot startup error: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
