#!/usr/bin/env python3
"""
Discord Vanity URL Sniper Bot - Fixed Version
"""

import subprocess
import sys

def install_package(package):
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", package])
        print(f"✓ Installed {package}")
    except:
        return False
    return True

# Check dependencies
try:
    import discord
    from discord import app_commands
    from discord.ext import tasks, commands
    import aiohttp
except ImportError:
    print("Installing dependencies...")
    install_package("discord.py")
    install_package("aiohttp")
    print("Restarting...\n")
    import discord
    from discord import app_commands
    from discord.ext import tasks, commands
    import aiohttp

import asyncio
import time
from datetime import datetime
from typing import Dict, List
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('VanitySniper')


class VanitySniper(commands.Bot):
    def __init__(self):
        # CONFIGURATION - EDIT THESE VALUES
        self.TOKEN = "MTI4OTY0NDkxNjUxNTAxNjgxOQ.GP7GL0.jdGTxp59ZQGAPEW6kz874Ds3sfFUDTqZwY00l0"
        self.GUILD_ID = 1498657544585875516  # Your server ID
        self.CHECK_INTERVAL = 1.0
        self.RETRY_DELAY = 5.0
        
        # State
        self.monitoring: Dict[str, dict] = {}
        self.claimed_urls: List[str] = []
        self.stats = {"attempts": 0, "claimed": 0, "errors": 0}
        self.API_BASE = "https://discord.com/api/v10"
        
        # Intents
        intents = discord.Intents.default()
        intents.guilds = True
        intents.message_content = True
        
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
        # DON'T create a new tree - Bot already has self.tree
    
    async def setup_hook(self):
        """Setup slash commands - NO tree creation here"""
        await self._register_commands()
        
        # Sync to specific guild (faster than global)
        guild = discord.Object(id=self.GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        logger.info("Slash commands synced")
    
    async def _register_commands(self):
        """Register all slash commands"""
        
        @self.tree.command(name="snipe", description="Start sniping a vanity URL")
        @app_commands.describe(
            vanity_code="The vanity URL code to snipe",
            attempts="Maximum attempts (0 = unlimited)"
        )
        async def snipe(interaction: discord.Interaction, vanity_code: str, attempts: int = 0):
            await interaction.response.defer(ephemeral=True)
            
            code = vanity_code.lower().strip()
            if not self._validate_code(code):
                await interaction.followup.send("❌ Invalid code (3-32 chars, a-z, 0-9, -, _)", ephemeral=True)
                return
            
            if code in self.monitoring:
                await interaction.followup.send(f"⚠️ Already monitoring `{code}`", ephemeral=True)
                return
            
            self.monitoring[code] = {
                "status": "active",
                "attempts": 0,
                "max_attempts": attempts if attempts > 0 else float('inf'),
                "start_time": time.time(),
                "channel_id": interaction.channel_id,
                "user_id": interaction.user.id
            }
            
            if not self.snipe_task.is_running():
                self.snipe_task.start()
            
            embed = discord.Embed(
                title="🎯 Vanity Sniper Started",
                description=f"Monitoring: **`{code}`**",
                color=discord.Color.green()
            )
            embed.add_field(name="Interval", value=f"{self.CHECK_INTERVAL}s")
            await interaction.followup.send(embed=embed)
        
        @self.tree.command(name="stop", description="Stop sniping a vanity URL")
        @app_commands.describe(vanity_code="The vanity code to stop")
        async def stop(interaction: discord.Interaction, vanity_code: str):
            code = vanity_code.lower().strip()
            if code in self.monitoring:
                del self.monitoring[code]
                await interaction.response.send_message(f"✅ Stopped `{code}`", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ Not monitoring `{code}`", ephemeral=True)
        
        @self.tree.command(name="stopall", description="Stop all sniping")
        async def stopall(interaction: discord.Interaction):
            count = len(self.monitoring)
            self.monitoring.clear()
            if self.snipe_task.is_running():
                self.snipe_task.stop()
            await interaction.response.send_message(f"🛑 Stopped {count} snipe(s)", ephemeral=True)
        
        @self.tree.command(name="status", description="View sniper status")
        async def status(interaction: discord.Interaction):
            embed = discord.Embed(title="📊 Vanity Sniper Status", color=discord.Color.blue())
            
            if self.monitoring:
                text = ""
                for code, data in self.monitoring.items():
                    dur = time.time() - data['start_time']
                    text += f"`{code}`: {data['attempts']} tries ({dur:.0f}s)\n"
                embed.add_field(name="Active", value=text, inline=False)
            else:
                embed.add_field(name="Active", value="None", inline=False)
            
            embed.add_field(name="Stats", value=f"Attempts: {self.stats['attempts']}\nClaimed: {self.stats['claimed']}")
            
            if self.claimed_urls:
                embed.add_field(name="✅ Claimed", value=", ".join(f"`{u}`" for u in self.claimed_urls), inline=False)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        @self.tree.command(name="check", description="Check if vanity URL is available")
        @app_commands.describe(vanity_code="Code to check")
        async def check(interaction: discord.Interaction, vanity_code: str):
            await interaction.response.defer(ephemeral=True)
            
            code = vanity_code.lower().strip()
            available, info = await self._check_vanity(code)
            
            if available:
                await interaction.followup.send(f"✅ `{code}` is **AVAILABLE**!", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ `{code}` is taken", ephemeral=True)
        
        @self.tree.command(name="claim", description="Try to claim a vanity URL now")
        @app_commands.describe(vanity_code="Code to claim")
        async def claim(interaction: discord.Interaction, vanity_code: str):
            await interaction.response.defer(ephemeral=True)
            
            code = vanity_code.lower().strip()
            if not self._validate_code(code):
                await interaction.followup.send("❌ Invalid code format", ephemeral=True)
                return
            
            result = await self._attempt_claim(code)
            
            if result["success"]:
                self.claimed_urls.append(code)
                self.stats["claimed"] += 1
                await interaction.followup.send(f"🎉 **SUCCESS!** Claimed `{code}`!", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Failed: {result.get('error', 'Unknown')}", ephemeral=True)
        
        @self.tree.command(name="help", description="Show commands")
        async def help_cmd(interaction: discord.Interaction):
            cmds = """
            `/snipe <code> [attempts]` - Start monitoring
            `/stop <code>` - Stop monitoring one
            `/stopall` - Stop all
            `/status` - Show stats
            `/check <code>` - Check availability
            `/claim <code>` - Claim now
            """
            embed = discord.Embed(title="Vanity Sniper Help", description=cmds, color=discord.Color.blue())
            await interaction.response.send_message(embed=embed, ephemeral=True)
    
    def _validate_code(self, code: str) -> bool:
        if not code or len(code) < 3 or len(code) > 32:
            return False
        return all(c in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in code)
    
    async def _api_request(self, endpoint: str, method: str = "GET", data: dict = None):
        headers = {
            "Authorization": f"Bot {self.TOKEN}",
            "Content-Type": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            url = f"{self.API_BASE}{endpoint}"
            try:
                if method == "GET":
                    async with session.get(url, headers=headers) as resp:
                        return resp.status, await resp.json() if resp.content_type == 'application/json' else None
                elif method == "PATCH":
                    async with session.patch(url, headers=headers, json=data) as resp:
                        return resp.status, await resp.json() if resp.content_type == 'application/json' else None
            except Exception as e:
                return 0, str(e)
    
    async def _check_vanity(self, code: str):
        status, data = await self._api_request(f"/invites/{code}")
        return (status == 404), data
    
    async def _attempt_claim(self, code: str):
        available, _ = await self._check_vanity(code)
        if not available:
            return {"success": False, "error": "Not available"}
        
        status, resp = await self._api_request(
            f"/guilds/{self.GUILD_ID}/vanity-url",
            method="PATCH",
            data={"code": code}
        )
        
        if status == 200:
            return {"success": True}
        elif status == 429:
            return {"success": False, "error": "Rate limited"}
        else:
            return {"success": False, "error": resp.get('message', f'HTTP {status}') if resp else f'HTTP {status}'}
    
    @tasks.loop(seconds=1.0)
    async def snipe_task(self):
        if not self.monitoring:
            self.snipe_task.stop()
            return
        
        for code in list(self.monitoring.keys()):
            monitor = self.monitoring[code]
            if monitor["status"] != "active":
                continue
            
            if monitor["attempts"] >= monitor["max_attempts"]:
                monitor["status"] = "completed"
                continue
            
            self.stats["attempts"] += 1
            monitor["attempts"] += 1
            
            available, _ = await self._check_vanity(code)
            
            if available:
                result = await self._attempt_claim(code)
                if result["success"]:
                    monitor["status"] = "claimed"
                    self.stats["claimed"] += 1
                    self.claimed_urls.append(code)
                    
                    channel = self.get_channel(monitor["channel_id"])
                    if channel:
                        await channel.send(f"🎉 **CLAIMED** discord.gg/{code}!")
                    
                    del self.monitoring[code]
                elif "Rate limited" in str(result.get("error", "")):
                    await asyncio.sleep(self.RETRY_DELAY)
            
            await asyncio.sleep(self.CHECK_INTERVAL / max(len(self.monitoring), 1))
    
    @snipe_task.before_loop
    async def before_snipe(self):
        await self.wait_until_ready()
    
    async def on_ready(self):
        print(f"\n{'='*40}")
        print(f"✅ Bot Online: {self.user}")
        print(f"Guild: {self.GUILD_ID}")
        print(f"Use /help for commands")
        print(f"{'='*40}\n")


def main():
    bot = VanitySniper()
    try:
        bot.run(bot.TOKEN)
    except discord.LoginFailure:
        print("\n❌ Invalid token! Edit TOKEN in the script.")
    except Exception as e:
        print(f"\n❌ Error: {e}")


if __name__ == "__main__":
    main()
