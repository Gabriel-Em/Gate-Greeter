import discord
from discord.ext import commands
import os
import json
import matplotlib.pyplot as plt
import io
from datetime import datetime, timedelta

class Analytics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="archive")
    async def archive(self, ctx):
        if os.getenv("ENABLE_CMD_ARCHIVE", "True").lower() not in ("true", "1", "t"):
            return # Command is disabled

        status_msg = await ctx.send("🗄️ Initializing 12-month deep scan. This will take a while...")
        os.makedirs('monthly_stats', exist_ok=True)
        now = datetime.now() 
        
        for i in range(12):
            y = now.year + (now.month - 1 - i) // 12
            m = (now.month - 1 - i) % 12 + 1
            
            start_dt = datetime(y, m, 1)
            end_dt = (datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)) - timedelta(seconds=1)
                
            is_complete = end_dt < now
            filename = f"monthly_stats/stats_{y}_{m:02d}.json"
            
            if os.path.exists(filename):
                try:
                    with open(filename, 'r') as f:
                        if json.load(f).get('complete', False):
                            continue
                except json.JSONDecodeError:
                    pass
                        
            await status_msg.edit(content=f"🗄️ Scraping data for {y}-{m:02d} (Month {i+1} of 12)...")
            month_data = {"complete": is_complete, "users": {}}
            total_channels = len(ctx.guild.text_channels)
            
            for idx, channel in enumerate(ctx.guild.text_channels, 1):
                if not channel.permissions_for(ctx.guild.me).read_message_history:
                    continue
                if channel.name.lower().endswith("logs"):
                    continue
                    
                if idx % 5 == 0:
                    await status_msg.edit(content=f"🗄️ Scraping data for {y}-{m:02d} (Month {i+1} of 12) — Scanning channel {idx}/{total_channels}...")
                    
                try:
                    async for msg in channel.history(limit=None, after=start_dt, before=end_dt):
                        if msg.author.bot: continue
                            
                        uid = str(msg.author.id)
                        cname = channel.name
                        
                        if uid not in month_data["users"]:
                            month_data["users"][uid] = {"name": str(msg.author), "channels": {}}
                        if cname not in month_data["users"][uid]["channels"]:
                            month_data["users"][uid]["channels"][cname] = 0
                            
                        month_data["users"][uid]["channels"][cname] += 1
                except discord.Forbidden:
                    pass
                    
            with open(filename, 'w') as f:
                json.dump(month_data, f, indent=4)
                
        await status_msg.edit(content="✅ 12-month data archive is up to date!")

    @commands.command(name="activity30")
    async def activity30(self, ctx, target_user_id: str = None):
        if os.getenv("ENABLE_CMD_ACTIVITY30", "True").lower() not in ("true", "1", "t"):
            return # Command is disabled

        if target_user_id:
            target_user_id = target_user_id.replace('<@', '').replace('!', '').replace('>', '')
            status_msg = await ctx.send(f"📈 Compiling metrics for user `{target_user_id}`...")
            title_context = f"User: {target_user_id}"
        else:
            status_msg = await ctx.send("📈 Compiling 30-day activity metrics for the server...")
            title_context = "Server-wide"
        
        now = datetime.now()
        start_dt = now - timedelta(days=30)
        hourly_counts = {i: 0 for i in range(24)}
        daily_counts = {(start_dt + timedelta(days=i)).strftime('%Y-%m-%d'): 0 for i in range(31)}

        for channel in ctx.guild.text_channels:
            if not channel.permissions_for(ctx.guild.me).read_message_history: continue
            if channel.name.lower().endswith("logs"): continue
                
            try:
                async for msg in channel.history(limit=None, after=start_dt):
                    if msg.author.bot: continue
                    if target_user_id and str(msg.author.id) != target_user_id: continue
                    
                    local_time = msg.created_at + timedelta(hours=3)
                    hourly_counts[local_time.hour] += 1
                    
                    day_str = msg.created_at.strftime('%Y-%m-%d')
                    if day_str in daily_counts:
                        daily_counts[day_str] += 1
            except discord.Forbidden:
                pass

        plt.figure(figsize=(10, 5))
        plt.bar(list(hourly_counts.keys()), list(hourly_counts.values()), color='#3b82f6')
        plt.title(f"Activity by Hour (Local Time) [{title_context}]")
        plt.xlabel("Hour (0:00 - 23:00)")
        plt.ylabel("Total Messages")
        plt.xticks(range(24))
        plt.grid(axis='y', linestyle='--', alpha=0.7)
        plt.tight_layout()
        
        buf_hourly = io.BytesIO()
        plt.savefig(buf_hourly, format="png")
        buf_hourly.seek(0)
        plt.close()

        plt.figure(figsize=(12, 5))
        sorted_days = sorted(daily_counts.items())
        plt.plot([i[0] for i in sorted_days], [i[1] for i in sorted_days], marker='o', color='#10b981', linewidth=2)
        plt.title(f"Daily Activity (Last 30 Days) [{title_context}]")
        plt.xlabel("Date")
        plt.ylabel("Messages")
        plt.xticks(rotation=45, ha='right')
        plt.grid(True, linestyle='--', alpha=0.5)
        plt.tight_layout()
        
        buf_daily = io.BytesIO()
        plt.savefig(buf_daily, format="png")
        buf_daily.seek(0)
        plt.close()

        file_hourly = discord.File(fp=buf_hourly, filename="hourly_activity.png")
        file_daily = discord.File(fp=buf_daily, filename="daily_activity.png")
        
        await status_msg.delete()
        await ctx.send(f"Here is the breakdown for the last 30 days ({title_context}):", files=[file_hourly, file_daily])

    @commands.command(name="topusers")
    async def topusers(self, ctx, year: int = None, month: int = None):
        if os.getenv("ENABLE_CMD_TOPUSERS", "True").lower() not in ("true", "1", "t"):
            return # Command is disabled

        if year is None:
            await ctx.send("⚠️ Please provide at least a year. Example: `!topusers 2026` or `!topusers 2026 9`")
            return

        now = datetime.now()
        
        if month is None:
            target_months = list(range(1, 13))
            title = f"{year} Yearly Total"
        else:
            if not (1 <= month <= 12): return await ctx.send("⚠️ Month must be between 1 and 12.")
            target_months = [month]
            title = f"{year}-{month:02d}"

        os.makedirs('monthly_stats', exist_ok=True)
        aggregated_users = {}
        all_months_complete = True
        status_msg = None
        total_channels = len(ctx.guild.text_channels)

        for m in target_months:
            start_dt = datetime(year, m, 1)
            if start_dt > now: continue

            end_dt = (datetime(year + 1, 1, 1) if m == 12 else datetime(year, m + 1, 1)) - timedelta(seconds=1)
            is_complete = end_dt < now
            filename = f"monthly_stats/stats_{year}_{m:02d}.json"
            
            needs_scrape = False
            if not os.path.exists(filename):
                needs_scrape = True
            else:
                try:
                    with open(filename, 'r') as f:
                        if not json.load(f).get('complete', False): needs_scrape = True
                except json.JSONDecodeError:
                    needs_scrape = True

            if needs_scrape:
                all_months_complete = False
                if not status_msg: status_msg = await ctx.send(f"⏳ Gathering missing data for **{title}**...")
                else: await status_msg.edit(content=f"⏳ Gathering missing data for **{title}** (Scanning {year}-{m:02d})...")

                month_data = {"complete": is_complete, "users": {}}

                for idx, channel in enumerate(ctx.guild.text_channels, 1):
                    if not channel.permissions_for(ctx.guild.me).read_message_history: continue
                    if channel.name.lower().endswith("logs"): continue
                        
                    if idx % 5 == 0: await status_msg.edit(content=f"⏳ Gathering data for **{title}** (Scanning {year}-{m:02d}, channel {idx}/{total_channels})...")
                        
                    try:
                        async for msg in channel.history(limit=None, after=start_dt, before=end_dt):
                            if msg.author.bot: continue
                            uid, uname, cname = str(msg.author.id), str(msg.author), channel.name
                            
                            if uid not in month_data["users"]: month_data["users"][uid] = {"name": uname, "channels": {}}
                            if cname not in month_data["users"][uid]["channels"]: month_data["users"][uid]["channels"][cname] = 0
                            month_data["users"][uid]["channels"][cname] += 1
                    except discord.Forbidden: pass

                with open(filename, 'w') as f: json.dump(month_data, f, indent=4)
                existing_data = month_data

            users_dict = existing_data.get("users", {})
            for uid, uinfo in users_dict.items():
                if uid not in aggregated_users: aggregated_users[uid] = {"name": uinfo.get("name", "Unknown User"), "total": 0}
                aggregated_users[uid]["total"] += sum(uinfo.get("channels", {}).values())

        if status_msg: await status_msg.delete()

        if not aggregated_users: return await ctx.send(f"ℹ️ No messages recorded for {title}.")

        user_totals = [(info["name"], info["total"]) for info in aggregated_users.values()]
        user_totals.sort(key=lambda x: x[1], reverse=True)
        top_10 = user_totals[:10]

        embed = discord.Embed(title=f"🏆 Top Active Users ({title})", color=discord.Color.gold())
        embed.description = "\n".join([f"**{rank}.** {name} — {count:,} messages" for rank, (name, count) in enumerate(top_10, 1)])
        embed.set_footer(text="Status: Complete (Loaded from Cache)" if all_months_complete else "Status: Data Updated to Present")
        
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Analytics(bot))