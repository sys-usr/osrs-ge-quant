package net.runelite.client.plugins.gequant;

import net.runelite.client.config.Config;
import net.runelite.client.config.ConfigGroup;
import net.runelite.client.config.ConfigItem;

@ConfigGroup("gequant")
public interface GeQuantConfig extends Config
{
    @ConfigItem(
        keyName = "apiUrl",
        name = "API Backend URL",
        description = "URL of your local OSRS GE Quant Flask server (e.g. http://127.0.0.1:8050)",
        position = 1
    )
    default String apiUrl()
    {
        return "http://127.0.0.1:8050";
    }

    @ConfigItem(
        keyName = "syncInterval",
        name = "Sync Interval (Seconds)",
        description = "How often to sync player statistics and cash/bank status with the server",
        position = 2
    )
    default int syncInterval()
    {
        return 30;
    }

    @ConfigItem(
        keyName = "autoLogTrades",
        name = "Auto-Log Completed Trades",
        description = "Automatically log Grand Exchange flips to the DB when they complete in-game",
        position = 3
    )
    default boolean autoLogTrades()
    {
        return true;
    }

    @ConfigItem(
        keyName = "showVisualOverlays",
        name = "GE Slot Target Overlays",
        description = "Draw suggested buy/sell price boundaries directly on top of active Grand Exchange offer slots",
        position = 4
    )
    default boolean showVisualOverlays()
    {
        return true;
    }
}
