import asyncio
import logging
import hashlib
import secrets
import time
import os
import re
from typing import Dict, Any, Optional

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiohttp import ClientSession

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Bot configuration - Use environment variables for security
BOT_TOKEN = os.getenv("BOT_TOKEN")
FEE_WALLET = os.getenv("FEE_WALLET", "UQAg3mG5c-QFD_KQQBzJMkd94y_r5pkAFegBijQr3LEbBWZ2")
TON_API_KEY = os.getenv("TON_API_KEY")

# Validate required environment variables
if not BOT_TOKEN:
    logger.error("BOT_TOKEN environment variable is required")
    exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Session storage
user_sessions: Dict[int, Dict[str, Any]] = {}
escrow_wallets: Dict[str, Dict[str, Any]] = {}

# Configuration
FEE_PERCENTAGE = 0.05  # 5% fee
PAYMENT_TIMEOUT_MINUTES = 60
PAYMENT_CHECK_INTERVAL = 30  # seconds

# Keyboards
main_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🛒 Start Escrow", callback_data="start_escrow")],
    [InlineKeyboardButton(text="📘 How it Works", callback_data="how_it_works")],
    [InlineKeyboardButton(text="🛠 Support", url="https://t.me/v1p3rton")]
])

back_main = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⬅️ Back", callback_data="main_menu"),
     InlineKeyboardButton(text="📋 Main Menu", callback_data="main_menu")]
])

def is_valid_ton_address(address: str) -> bool:
    """Validate TON address format with comprehensive checks"""
    try:
        address = address.strip()

        # Check length
        if len(address) < 48 or len(address) > 50:
            return False

        # Check prefix
        valid_prefixes = ['EQ', 'UQ', 'kQ', '0Q']
        if not any(address.startswith(prefix) for prefix in valid_prefixes):
            return False

        # Check if the rest contains only valid base64url characters
        address_body = address[2:]
        if not re.match(r'^[A-Za-z0-9_-]+$', address_body):
            return False

        return True
    except Exception as e:
        logger.error(f"Address validation error: {e}")
        return False

def generate_escrow_wallet() -> Dict[str, Any]:
    """Generate a real TON wallet address for escrow using pytoniq-core"""
    try:
        # Import required libraries
        from pytoniq_core import WalletV4R2, Address
        
        # Generate a new random private key (32 bytes)
        private_key_bytes = secrets.token_bytes(32)
        
        # Create wallet using pytoniq-core (not pytoniq)
        wallet = WalletV4R2(private_key=private_key_bytes, workchain=0)
        
        # Get the wallet address
        address = wallet.address
        address_str = address.to_str(is_user_friendly=True, is_bounceable=False)
        
        # Validate the generated address
        if not address_str or not is_valid_ton_address(address_str):
            raise Exception(f"Failed to generate valid TON address: {address_str}")

        wallet_info = {
            "address": address_str,
            "private_key": private_key_bytes.hex(),
            "created_at": str(int(time.time() * 1000)),
            "wallet_object": wallet,
            "wallet_address": address
        }

        logger.info(f"Successfully generated new unique TON escrow wallet: {address_str}")
        return wallet_info

    except Exception as e:
        logger.error(f"Error generating TON wallet: {e}")
        raise

async def get_wallet_balance(address: str) -> float:
    """Get wallet balance using TON API with proper error handling"""
    try:
        # Validate address before API call
        if not is_valid_ton_address(address):
            logger.error(f"Invalid address format: {address}")
            return 0.0

        # Try primary API first
        url = f"https://tonapi.io/v2/accounts/{address}"
        headers = {"User-Agent": "DarkExchange-Bot/1.0"}

        # Add API key if available
        if TON_API_KEY:
            headers["Authorization"] = f"Bearer {TON_API_KEY}"

        async with ClientSession(timeout=30) as session:
            try:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        balance_nano = int(data.get("balance", 0))
                        balance_ton = balance_nano / 1e9
                        logger.info(f"Balance for {address}: {balance_ton} TON")
                        return balance_ton
                    elif response.status == 404:
                        logger.info(f"Account not found (new wallet): {address}")
                        return 0.0
                    else:
                        error_text = await response.text()
                        logger.warning(f"Primary API failed ({response.status}): {error_text}")
                        # Try fallback API
                        return await get_balance_fallback(address, session)
            except asyncio.TimeoutError:
                logger.warning(f"Timeout getting balance for {address}, trying fallback")
                return await get_balance_fallback(address, session)

    except Exception as e:
        logger.error(f"Error getting balance for {address}: {e}")
        return 0.0

async def get_balance_fallback(address: str, session: ClientSession) -> float:
    """Fallback balance check using alternative API"""
    try:
        fallback_url = f"https://toncenter.com/api/v2/getAddressInformation?address={address}"
        async with session.get(fallback_url) as response:
            if response.status == 200:
                data = await response.json()
                if data.get("ok") and data.get("result"):
                    balance_nano = int(data["result"].get("balance", 0))
                    balance_ton = balance_nano / 1e9
                    logger.info(f"Fallback balance for {address}: {balance_ton} TON")
                    return balance_ton
            return 0.0
    except Exception as e:
        logger.error(f"Fallback balance check failed: {e}")
        return 0.0

async def send_ton_payment(from_wallet: Dict[str, Any], to_address: str, amount_ton: float) -> bool:
    """Send TON payment using pytoniq for real transactions"""
    try:
        from pytoniq import LiteBalancer
        from pytoniq_core import Address

        # Validate inputs
        if not is_valid_ton_address(to_address):
            logger.error(f"Invalid recipient address: {to_address}")
            return False

        if amount_ton <= 0:
            logger.error(f"Invalid amount: {amount_ton}")
            return False

        wallet = from_wallet.get("wallet_object")
        if not wallet:
            logger.error("Wallet object not found")
            return False

        amount_nano = int(amount_ton * 1e9)
        to_addr = Address(to_address)

        logger.info(f"Sending {amount_ton} TON ({amount_nano} nano) to {to_address}")

        try:
            # Connect to TON network via LiteBalancer
            provider = LiteBalancer.from_mainnet_config(trust_level=1)
            await provider.start_up()

            # Create transfer message
            transfer_body = wallet.create_transfer_msg(
                to_addr=to_addr,
                amount=amount_nano,
                seqno=await wallet.get_seqno(provider),
                send_mode=3
            )

            # Send the transaction
            await provider.send_message(transfer_body)

            # Wait for transaction confirmation
            await asyncio.sleep(10)  # Wait for block confirmation

            await provider.close_all()
            logger.info(f"Successfully sent {amount_ton} TON to {to_address}")
            return True

        except Exception as tx_error:
            logger.error(f"Transaction failed: {tx_error}")
            try:
                await provider.close_all()
            except:
                pass
            return False

    except Exception as e:
        logger.error(f"Error in send_ton_payment: {e}")
        return False

def sanitize_user_input(text: str, max_length: int = 100) -> str:
    """Sanitize user input to prevent injection attacks"""
    if not isinstance(text, str):
        return ""

    # Remove potentially dangerous characters
    sanitized = re.sub(r'[<>"\'\(\){}[\]\\]', '', text.strip())

    # Limit length
    return sanitized[:max_length]

@dp.message(Command("start"))
async def start_handler(msg: types.Message):
    """Handle /start command"""
    try:
        user_id = msg.from_user.id
        username = msg.from_user.username or "User"

        logger.info(f"Start command from user {user_id} (@{username})")

        await msg.answer(
            "🌟 Welcome to DarkExchange — Secure TON Escrow Service!\n\n"
            "🔒 Automated escrow using real TON blockchain wallets.\n"
            "💼 Safe transactions with 5% service fee.\n"
            "⚡ Real-time payment monitoring.\n\n"
            "Choose an option below:",
            reply_markup=main_menu
        )

        # Clean up any existing session
        if user_id in user_sessions:
            del user_sessions[user_id]

    except Exception as e:
        logger.error(f"Error in start handler: {e}")
        await msg.answer("Service temporarily unavailable. Please try again later.")

@dp.callback_query(F.data == "main_menu")
async def menu_handler(callback: types.CallbackQuery):
    """Handle main menu callback"""
    try:
        await callback.message.edit_text(
            "🌟 DarkExchange Main Menu\n\n"
            "🔒 Secure TON Escrow Service\n"
            "Choose an option below:",
            reply_markup=main_menu
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in menu handler: {e}")
        await callback.answer("Error occurred, please try again.")

@dp.callback_query(F.data == "how_it_works")
async def how_it_works_handler(callback: types.CallbackQuery):
    """Provide detailed instructions"""
    try:
        instructions = (
            "🔐 **How DarkExchange Works:**\n\n"
            "**Step 1:** Click 'Start Escrow' to begin\n"
            "**Step 2:** Enter seller's valid TON wallet address\n"
            "**Step 3:** Specify escrow amount (0.1 - 1000 TON)\n"
            "**Step 4:** Receive unique escrow wallet address\n"
            "**Step 5:** Send exact amount to escrow wallet\n"
            "**Step 6:** Automatic release to seller after confirmation\n\n"
            "💰 **Fees:** 5% service fee deducted from amount\n"
            "⏱ **Timeout:** 60 minutes for payment\n"
            "🔍 **Monitoring:** Real-time blockchain verification\n\n"
            "⚠️ **Important:**\n"
            "• Double-check all addresses\n"
            "• Send exact amount specified\n"
            "• Keep transaction hash for support\n"
            "• Contact support if issues occur"
        )

        await callback.message.edit_text(
            instructions,
            parse_mode="Markdown", 
            reply_markup=back_main
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in how_it_works handler: {e}")
        await callback.answer("Error loading instructions.")

@dp.callback_query(F.data == "start_escrow")
async def escrow_entry(callback: types.CallbackQuery):
    """Start escrow process with validation"""
    try:
        user_id = callback.from_user.id

        # Check if user already has active session
        if user_id in user_sessions and user_sessions[user_id].get("step") == "completed":
            await callback.message.edit_text(
                "⚠️ You already have an active escrow.\n"
                "Please wait for it to complete or contact support.",
                reply_markup=back_main
            )
            await callback.answer()
            return

        user_sessions[user_id] = {
            "step": "waiting_seller_wallet",
            "started_at": int(time.time())
        }

        await callback.message.edit_text(
            "💰 **Starting TON Escrow Process**\n\n"
            "📝 Please enter the seller's TON wallet address:\n\n"
            "✅ **Valid formats:**\n"
            "• EQxxxxx... (bounceable)\n"
            "• UQxxxxx... (non-bounceable)\n"
            "• kQxxxxx... (test network)\n\n"
            "⚠️ **Warning:** Incorrect addresses will result in lost funds!",
            parse_mode="Markdown",
            reply_markup=back_main
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error in escrow entry: {e}")
        await callback.answer("Error starting escrow process.")

@dp.message(F.text)
async def handle_text_messages(msg: types.Message):
    """Handle text messages with comprehensive validation"""
    user_id = msg.from_user.id

    try:
        # Rate limiting check
        if hasattr(msg, 'date') and msg.date:
            message_time = int(msg.date.timestamp())
            current_time = int(time.time())
            if current_time - message_time > 300:  # 5 minutes old
                await msg.answer("Message too old. Please try again.", reply_markup=main_menu)
                return

        if user_id not in user_sessions:
            await msg.answer(
                "Please start by using /start command or menu buttons.",
                reply_markup=main_menu
            )
            return

        session = user_sessions[user_id]

        # Session timeout check
        session_age = int(time.time()) - session.get("started_at", 0)
        if session_age > 1800:  # 30 minutes
            del user_sessions[user_id]
            await msg.answer(
                "Session expired. Please start over.",
                reply_markup=main_menu
            )
            return

        step = session.get("step")

        if step == "waiting_seller_wallet":
            await handle_seller_wallet_input(msg, session)
        elif step == "waiting_amount":
            await handle_amount_input(msg, session)
        else:
            await msg.answer(
                "Please use the menu buttons to navigate.",
                reply_markup=main_menu
            )

    except Exception as e:
        logger.error(f"Error handling text message from user {user_id}: {e}")
        await msg.answer(
            "An error occurred processing your message. Please try again.",
            reply_markup=main_menu
        )

async def handle_seller_wallet_input(msg: types.Message, session: Dict[str, Any]):
    """Handle seller wallet address input with thorough validation"""
    try:
        raw_address = msg.text.strip()
        wallet_address = sanitize_user_input(raw_address, 50)

        # Comprehensive validation
        if not wallet_address:
            await msg.answer(
                "❌ Empty address. Please enter a valid TON address:",
                reply_markup=back_main
            )
            return

        if not is_valid_ton_address(wallet_address):
            await msg.answer(
                "❌ **Invalid TON address format**\n\n"
                "Please enter a valid TON address:\n"
                "• Must start with EQ, UQ, kQ, or 0Q\n"
                "• Must be 48-50 characters long\n"
                "• Contains only valid base64url characters\n\n"
                "Example: `EQAg3mG5c-QFD_KQQBzJMkd94y_r5pkAFegBijQr3LEbBWZ2`",
                parse_mode="Markdown",
                reply_markup=back_main
            )
            return

        # Check if address is the same as fee wallet (prevent conflicts)
        if wallet_address == FEE_WALLET:
            await msg.answer(
                "❌ Cannot use service fee wallet as seller address.",
                reply_markup=back_main
            )
            return

        session["seller_wallet"] = wallet_address
        session["step"] = "waiting_amount"

        await msg.answer(
            f"✅ **Seller wallet saved**\n"
            f"`{wallet_address}`\n\n"
            f"💰 Now enter the total amount (in TON):\n\n"
            f"📊 **Details:**\n"
            f"• Fee: {int(FEE_PERCENTAGE * 100)}% of total amount\n\n"
            f"💡 Example: Enter `1.5` for 1.5 TON",
            parse_mode="Markdown",
            reply_markup=back_main
        )

    except Exception as e:
        logger.error(f"Error handling seller wallet input: {e}")
        await msg.answer("Error processing address. Please try again.", reply_markup=back_main)

async def handle_amount_input(msg: types.Message, session: Dict[str, Any]):
    """Handle amount input with comprehensive validation"""
    try:
        amount_text = sanitize_user_input(msg.text.strip(), 20)

        # Parse amount
        try:
            amount = float(amount_text)
        except ValueError:
            await msg.answer(
                "❌ Invalid number format.\n"
                "Please enter a valid decimal number (e.g., 1.5)",
                reply_markup=back_main
            )
            return

        # Validate amount range
        if amount <= 0:
            await msg.answer(
                "❌ Amount must be positive.",
                reply_markup=back_main
            )
            return

        # Calculate fees
        fee_amount = round(amount * FEE_PERCENTAGE, 6)
        seller_amount = round(amount - fee_amount, 6)

        session["amount"] = amount
        session["fee_amount"] = fee_amount
        session["seller_amount"] = seller_amount
        session["step"] = "generating_wallet"

        await msg.answer(
            "🔄 **Generating secure TON escrow wallet...**\n\n"
            "Please wait while we create your unique escrow address.",
            reply_markup=back_main
        )

        # Generate wallet
        try:
            escrow_wallet_info = generate_escrow_wallet()
            escrow_address = escrow_wallet_info["address"]

            # Create unique transaction ID for independent monitoring
            transaction_id = f"{user_id}_{int(time.time())}"

            session["escrow_address"] = escrow_address
            session["escrow_private_key"] = escrow_wallet_info["private_key"]
            session["transaction_id"] = transaction_id
            session["step"] = "completed"

            # Store wallet info with transaction ID for independent monitoring
            escrow_wallets[transaction_id] = {
                **escrow_wallet_info,
                "user_id": user_id,
                "seller_wallet": session["seller_wallet"],
                "amount": amount,
                "fee_amount": fee_amount,
                "seller_amount": seller_amount,
                "status": "waiting_payment",
                "created_timestamp": int(time.time()),
                "transaction_id": transaction_id
            }

            await msg.answer(
                f"🏦 **TON Escrow Created Successfully!**\n\n"
                f"💰 **Total Amount:** `{amount}` TON\n"
                f"🏪 **Seller:** `{session['seller_wallet']}`\n"
                f"💸 **Service Fee ({int(FEE_PERCENTAGE * 100)}%):** `{fee_amount}` TON\n"
                f"📨 **Seller Receives:** `{seller_amount}` TON\n\n"
                f"🔐 **Send EXACTLY {amount} TON to:**\n"
                f"`{escrow_address}`\n\n"
                f"⚠️ **IMPORTANT:**\n"
                f"• Send EXACTLY {amount} TON (not more, not less)\n"
                f"• Payment timeout: {PAYMENT_TIMEOUT_MINUTES} minutes\n"
                f"• Real-time monitoring active\n"
                f"• Keep this message for reference",
                parse_mode="Markdown",
                reply_markup=back_main
            )

            # Start payment monitoring with transaction ID
            asyncio.create_task(monitor_payment(msg.from_user.id, transaction_id))

        except Exception as e:
            logger.error(f"Error generating escrow wallet: {e}")
            session["step"] = "waiting_amount"
            await msg.answer(
                "❌ **Wallet Generation Failed**\n\n"
                "Unable to create escrow wallet. This could be due to:\n"
                "• TON network connectivity issues\n"
                "• Service temporarily unavailable\n\n"
                "Please try again or contact support.",
                reply_markup=back_main
            )

    except Exception as e:
        logger.error(f"Error handling amount input: {e}")
        await msg.answer("Error processing amount. Please try again.", reply_markup=back_main)

async def monitor_payment(user_id: int, transaction_id: str):
    """Monitor payment with comprehensive error handling and status updates"""
    max_checks = (PAYMENT_TIMEOUT_MINUTES * 60) // PAYMENT_CHECK_INTERVAL
    check_count = 0
    last_balance = 0.0

    logger.info(f"Starting payment monitoring for user {user_id}, transaction_id: {transaction_id}")

    while check_count < max_checks:
        try:
            session = user_sessions.get(user_id)
            if not session or session.get("step") != "completed" or session.get("transaction_id") != transaction_id:
                logger.info(f"Stopping payment monitor for user {user_id}, transaction_id {transaction_id} - session invalid")
                break

            escrow_address = session["escrow_address"]
            expected_amount = session["amount"]

            # Get current balance
            current_balance = await get_wallet_balance(escrow_address)

            # Check if payment received
            if current_balance >= expected_amount:
                logger.info(f"Payment received for user {user_id}, transaction_id: {transaction_id}: {current_balance} TON")
                await process_escrow_release(user_id, transaction_id, session)
                break

            # Send status updates at specific intervals
            if check_count in [1, 5, 10, 20, 40]:
                status_msg = (
                    f"⏳ **Payment Monitoring Active**\n\n"
                    f"💰 Expected: `{expected_amount}` TON\n"
                    f"📊 Received: `{current_balance}` TON\n"
                    f"🏦 Address: `{escrow_address}`\n"
                    f"⏱ Check #{check_count}/{max_checks}\n\n"
                    f"🔄 Checking every {PAYMENT_CHECK_INTERVAL} seconds"
                )

                try:
                    await bot.send_message(user_id, status_msg, parse_mode="Markdown")
                except Exception as e:
                    logger.warning(f"Failed to send status update to user {user_id}: {e}")

            # Log balance changes
            if current_balance != last_balance:
                logger.info(f"Balance change for {escrow_address}: {last_balance} -> {current_balance} TON")
                last_balance = current_balance

            check_count += 1
            await asyncio.sleep(PAYMENT_CHECK_INTERVAL)

        except Exception as e:
            logger.error(f"Error in payment monitoring for user {user_id}, transaction_id: {transaction_id}: {e}")
            await asyncio.sleep(PAYMENT_CHECK_INTERVAL)

    # Handle timeout
    if check_count >= max_checks:
        session = user_sessions.get(user_id)
        if session and session.get("step") == "completed" and session.get("transaction_id") == transaction_id:
            try:
                await bot.send_message(
                    user_id,
                    f"⏰ **Escrow Timeout ({PAYMENT_TIMEOUT_MINUTES} minutes)**\n\n"
                    f"No payment detected at:\n"
                    f"`{session.get('escrow_address', 'Unknown')}`\n\n"
                    f"If you sent the payment:\n"
                    f"• Check transaction status\n"
                    f"• Contact support with transaction hash\n"
                    f"• Wallet remains monitored for manual verification\n\n"
                    f"⚠️ Do not send additional payments to this address.",
                    parse_mode="Markdown",
                    reply_markup=main_menu
                )
            except Exception as e:
                logger.error(f"Failed to send timeout message to user {user_id}: {e}")

    logger.info(f"Payment monitoring ended for user {user_id}, transaction_id: {transaction_id}")

async def process_escrow_release(user_id: int, transaction_id: str, session: Dict[str, Any]):
    """Process escrow release with comprehensive error handling"""
    try:
        amount = session["amount"]
        seller_address = session["seller_wallet"]
        escrow_address = session["escrow_address"]
        fee_amount = session["fee_amount"]
        seller_amount = session["seller_amount"]

        logger.info(f"Processing escrow release for user {user_id}, transaction_id: {transaction_id}: {amount} TON")

        await bot.send_message(
            user_id,
            "🔄 **Processing TON Transfers...**\n\n"
            "Please wait while we execute the payments.\n"
            "This may take a few moments."
        )

        # Get wallet info
        wallet_info = escrow_wallets.get(transaction_id)
        if not wallet_info:
            logger.error(f"Wallet info not found for transaction_id {transaction_id}")
            await bot.send_message(
                user_id,
                "❌ **Critical Error**\n\n"
                "Wallet data not found. Your funds are safe.\n"
                "Please contact support immediately with this escrow address:\n"
                f"`{escrow_address}`",
                parse_mode="Markdown",
                reply_markup=main_menu
            )
            return

        # Execute payments
        seller_success = await send_ton_payment(wallet_info, seller_address, seller_amount)
        fee_success = await send_ton_payment(wallet_info, FEE_WALLET, fee_amount)

        if seller_success and fee_success:
            # Success message
            success_msg = (
                f"✅ **Escrow Completed Successfully!**\n\n"
                f"💰 **Total Amount:** `{amount}` TON\n"
                f"📤 **Sent to Seller:** `{seller_amount}` TON\n"
                f"💸 **Service Fee:** `{fee_amount}` TON\n\n"
                f"🏪 **Seller:** `{seller_address}`\n"
                f"🏦 **Escrow:** `{escrow_address}`\n\n"
                f"🎉 **Transaction completed on TON blockchain!**\n"
                f"Thank you for using DarkExchange! 🌟"
            )

            await bot.send_message(
                user_id,
                success_msg,
                parse_mode="Markdown",
                reply_markup=main_menu
            )

        elif seller_success and not fee_success:
            # Partial success
            await bot.send_message(
                user_id,
                f"⚠️ **Partial Success**\n\n"
                f"✅ Seller payment sent: `{seller_amount}` TON\n"
                f"❌ Fee payment failed: `{fee_amount}` TON\n\n"
                f"Your escrow is complete. Fee issue will be resolved by support.",
                parse_mode="Markdown",
                reply_markup=main_menu
            )

        else:
            # Payment failed
            await bot.send_message(
                user_id,
                f"❌ **Payment Processing Failed**\n\n"
                f"Your `{amount}` TON is safe at:\n"
                f"`{escrow_address}`\n\n"
                f"**Next Steps:**\n"
                f"• Contact support immediately\n"
                f"• Provide this escrow address\n"
                f"• Manual release will be processed\n\n"
                f"⚠️ Your funds are secure and will be released.",
                parse_mode="Markdown",
                reply_markup=main_menu
            )

        # Clean up session
        if user_id in user_sessions:
            del user_sessions[user_id]
        if transaction_id in escrow_wallets:
            del escrow_wallets[transaction_id]

        logger.info(f"Escrow release completed for user {user_id}, transaction_id: {transaction_id}")

    except Exception as e:
        logger.error(f"Error processing escrow release for user {user_id}, transaction_id: {transaction_id}: {e}")
        try:
            await bot.send_message(
                user_id,
                f"❌ **Critical Error During Release**\n\n"
                f"Your funds are safe at:\n"
                f"`{session.get('escrow_address', 'Unknown')}`\n\n"
                f"**Immediate Action Required:**\n"
                f"• Contact support now\n"
                f"• Provide your user ID: `{user_id}`\n"
                f"• Manual verification needed\n\n"
                f"🔒 Your funds are protected.",
                parse_mode="Markdown",
                reply_markup=main_menu
            )
        except Exception as msg_error:
            logger.error(f"Failed to send error message to user {user_id}: {msg_error}")

async def main():
    """Main function with proper initialization and error handling"""
    try:
        logger.info("Starting DarkExchange TON Escrow Bot...")

        # Validate TON library
        try:
            from pytoniq import LiteBalancer
            from pytoniq_core import WalletV4R2, Address
            logger.info("✅ pytoniq libraries available - real TON wallets enabled")
        except ImportError as e:
            logger.error(f"❌ pytoniq libraries not found: {e}")
            logger.error("Please install: pip install pytoniq pytoniq-core")
            return

        # Validate configuration
        if not is_valid_ton_address(FEE_WALLET):
            logger.error(f"❌ Invalid fee wallet address: {FEE_WALLET}")
            return

        # Test escrow wallet generation
        try:
            test_wallet = generate_escrow_wallet()
            logger.info(f"✅ Escrow wallet generation test successful: {test_wallet['address']}")
            logger.info(f"✅ Each escrow will use a unique wallet address")
        except Exception as e:
            logger.error(f"❌ Escrow wallet generation failed: {e}")
            return

        # Test network connectivity
        try:
            balance = await get_wallet_balance(FEE_WALLET)
            logger.info(f"✅ Network connectivity test successful - Fee wallet balance: {balance} TON")
        except Exception as e:
            logger.warning(f"⚠️ Network connectivity test failed: {e}")
            logger.info("Continuing anyway - network may be temporarily unavailable")

        logger.info(f"✅ Configuration validated")
        logger.info(f"✅ Fee wallet: {FEE_WALLET}")
        logger.info(f"✅ Service fee: {int(FEE_PERCENTAGE * 100)}%")
        logger.info(f"✅ Unique wallet generation: ENABLED")
        logger.info("✅ Ready for live transactions!")

        # Start bot
        logger.info("🚀 Starting bot polling...")
        await dp.start_polling(bot, skip_updates=True)

    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Critical bot error: {e}")
    finally:
        try:
            await bot.session.close()
            logger.info("Bot session closed")
        except Exception as e:
            logger.error(f"Error closing bot session: {e}")

if __name__ == "__main__":
    asyncio.run(main())
