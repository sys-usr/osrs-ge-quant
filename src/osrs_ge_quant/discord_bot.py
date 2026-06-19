# src/osrs_ge_quant/discord_bot.py
import os
import asyncio
import threading
from discord.ext import commands
import discord

from .ledger import get_consolidated_ledger, allocate_buy_order
from .automation import GeAutomationBot

# Configuration
BOT_PAUSED_FLAG = os.path.join("data", "bot_paused.flag")

def is_bot_paused() -> bool:
    return os.path.exists(BOT_PAUSED_FLAG)

def set_bot_paused(paused: bool):
    os.makedirs("data", exist_ok=True)
    if paused:
        with open(BOT_PAUSED_FLAG, "w") as f:
            f.write("paused")
    else:
        if os.path.exists(BOT_PAUSED_FLAG):
            os.remove(BOT_PAUSED_FLAG)

# Init bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"[Discord] Bot logged in as {bot.user.name} (ID: {bot.user.id})")

@bot.command(name="portfolio")
async def cmd_portfolio(ctx):
    """Shows the consolidated ledger balance sheet and holdings across all alts."""
    try:
        ledger = get_consolidated_ledger()
        
        embed = discord.Embed(
            title="OSRS GE Quant - Consolidated Portfolio",
            color=discord.Color.gold(),
            timestamp=ctx.message.created_at
        )
        
        # Balance Summary
        summary_text = (
            f"**Total Cash:** {ledger['total_cash']:,.0f} GP\n"
            f"**Holdings Value:** {ledger['total_holdings_value']:,.0f} GP\n"
            f"**Total Net Worth:** {ledger['total_net_worth']:,.0f} GP"
        )
        embed.add_field(name="Summary", value=summary_text, inline=False)
        
        # Accounts list
        acc_text = ""
        for acc in ledger["accounts"]:
            acc_text += f"• **{acc['account_name']}:** Cash: {acc['cash']:,.0f} GP | Holdings: {acc['holdings_value']:,.0f} GP\n"
        if acc_text:
            embed.add_field(name="Accounts Cash & Value", value=acc_text, inline=False)
            
        # Holdings list
        holdings_text = ""
        for h in ledger["holdings"][:10]: # Limit to top 10 items
            pnl_sign = "+" if h["unrealized_pnl"] >= 0 else ""
            holdings_text += (
                f"• **{h['item_name']}** ({h['qty']:,.0f}x) on *{h['account_name']}*\n"
                f"  Avg Cost: {h['avg_cost']:,.0f} GP | P&L: {pnl_sign}{h['unrealized_pnl']:,.0f} GP\n"
            )
        if len(ledger["holdings"]) > 10:
            holdings_text += f"...and {len(ledger['holdings']) - 10} more items."
            
        if holdings_text:
            embed.add_field(name="Active Holdings", value=holdings_text, inline=False)
        else:
            embed.add_field(name="Active Holdings", value="No open positions.", inline=False)
            
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"Error loading portfolio: `{e}`")

@bot.command(name="status")
async def cmd_status(ctx):
    """Shows the execution status of the trading bot and current overlay alignment."""
    try:
        paused = is_bot_paused()
        state_str = "⏸️ PAUSED" if paused else "▶️ RUNNING"
        
        # Fetch coordinates to see if RuneLite coordinates are active
        autobot = GeAutomationBot()
        coords = autobot.fetch_coordinates()
        sync_status = "✅ CONNECTED" if coords else "❌ DISCONNECTED"
        
        embed = discord.Embed(title="OSRS GE Quant - Bot Status", color=discord.Color.blue())
        embed.add_field(name="Trading Status", value=state_str, inline=True)
        embed.add_field(name="RuneLite Coordinate Sync", value=sync_status, inline=True)
        
        if coords:
            # List some of the detected widget keys
            detected_keys = ", ".join(list(coords.keys())[:8])
            embed.add_field(name="Scanned UI Elements", value=f"`{detected_keys}`", inline=False)
            
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"Error checking status: `{e}`")

@bot.command(name="pause")
async def cmd_pause(ctx):
    """Pauses the automation loops."""
    set_bot_paused(True)
    await ctx.send("⏸️ Trading bot execution has been **PAUSED**.")

@bot.command(name="resume")
async def cmd_resume(ctx):
    """Resumes the automation loops."""
    set_bot_paused(False)
    await ctx.send("▶️ Trading bot execution has been **RESUMED**.")

@bot.command(name="trade")
async def cmd_trade(ctx, side: str, qty: int, price: int, *, item_name: str):
    """
    Manually triggers an automated trade.
    Example: !trade buy 50 1200 Cannonball
    """
    if side.lower() not in ["buy", "sell"]:
        await ctx.send("Usage: `!trade [buy/sell] [qty] [price] [item name]`")
        return
        
    await ctx.send(f"🤖 Initiating automated **{side.upper()}** order: {qty}x **{item_name}** @ {price} GP...")
    
    # We execute this inside an executor to avoid blocking the asyncio event loop
    loop = asyncio.get_event_loop()
    def run_trade():
        autobot = GeAutomationBot()
        # For simplicity, we default to Slot 0
        return autobot.place_offer(side=side, slot=0, item_name=item_name, qty=qty, price=price)
        
    success = await loop.run_in_executor(None, run_trade)
    if success:
        await ctx.send(f"✅ Trade successfully executed and confirmed on RuneLite client!")
    else:
        await ctx.send(f"❌ Trade execution failed. Check coordinate sync status or client window focus.")

def start_discord_bot():
    """Starts the Discord bot client in the background if a token exists."""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("[Discord] DISCORD_BOT_TOKEN is not configured in .env file. Bot will not run.")
        return
        
    def run_thread():
        # discord.py requires its own event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(bot.start(token))
        
    t = threading.Thread(target=run_thread, daemon=True)
    t.start()
    print("[Discord] Background bot thread started.")
