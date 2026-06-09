package net.runelite.client.plugins.gequant;

import com.google.gson.Gson;
import com.google.inject.Provides;
import java.io.IOException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import javax.inject.Inject;
import lombok.extern.slf4j.Slf4j;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.GrandExchangeOffer;
import net.runelite.api.GrandExchangeOfferState;
import net.runelite.api.InventoryID;
import net.runelite.api.Item;
import net.runelite.api.ItemContainer;
import net.runelite.api.ItemID;
import net.runelite.api.Skill;
import net.runelite.api.events.GameStateChanged;
import net.runelite.api.events.GrandExchangeOfferChanged;
import net.runelite.api.events.ItemContainerChanged;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.game.ItemManager;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDescriptor;
import net.runelite.client.ui.ClientToolbar;
import net.runelite.client.ui.NavigationButton;
import net.runelite.client.util.ImageUtil;
import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

@Slf4j
@PluginDescriptor(
    name = "GE Quant Partner",
    description = "Pushes bank value, player stats, and logs completed flips to the GE Quant local terminal.",
    tags = {"grand exchange", "flip", "trading", "quant", "processing"}
)
public class GeQuantPlugin extends Plugin
{
    @Inject
    private Client client;

    @Inject
    private GeQuantConfig config;

    @Inject
    private ItemManager itemManager;

    @Inject
    private ClientToolbar clientToolbar;

    @Inject
    private OkHttpClient okHttpClient;

    @Inject
    private Gson gson;

    @Inject
    private ScheduledExecutorService executor;

    private GeQuantPanel panel;
    private NavigationButton navButton;

    private long lastSyncTime = 0;
    private long totalBankValue = 0;
    private long pureCashValue = 0;
    private long itemValue = 0;

    private final Map<Integer, GrandExchangeOfferState> previousOfferStates = new HashMap<>();

    @Override
    protected void startUp() throws Exception
    {
        panel = injector.getInstance(GeQuantPanel.class);
        panel.init(this);

        navButton = NavigationButton.builder()
            .tooltip("GE Quant Partner")
            .icon(ImageUtil.loadImageResource(getClass(), "gquant_icon.png"))
            .priority(6)
            .panel(panel)
            .build();

        clientToolbar.addNavigation(navButton);

        // Schedule periodic synchronization
        executor.scheduleWithFixedDelay(this::syncPlayerProfile, 5, config.syncInterval(), TimeUnit.SECONDS);
        log.info("GE Quant Partner Plugin started.");
    }

    @Override
    protected void shutDown() throws Exception
    {
        clientToolbar.removeNavigation(navButton);
        log.info("GE Quant Partner Plugin stopped.");
    }

    @Provides
    GeQuantConfig provideConfig(ConfigManager configManager)
    {
        return configManager.getConfig(GeQuantConfig.class);
    }

    @Subscribe
    public void onGameStateChanged(GameStateChanged gameStateChanged)
    {
        if (gameStateChanged.getGameState() == GameState.LOGGED_IN)
        {
            panel.updatePlayerName(client.getLocalPlayer().getName());
        }
    }

    @Subscribe
    public void onItemContainerChanged(ItemContainerChanged event)
    {
        if (event.getContainerId() != InventoryID.BANK.getId())
        {
            return;
        }

        ItemContainer bankContainer = event.getItemContainer();
        if (bankContainer == null)
        {
            return;
        }

        long tempCash = 0;
        long tempItemVal = 0;

        for (Item item : bankContainer.getItems())
        {
            int itemId = item.getId();
            long qty = item.getQuantity();

            if (itemId == ItemID.COINS)
            {
                tempCash += qty;
            }
            else
            {
                long price = itemManager.getItemPrice(itemId);
                tempItemVal += (price * qty);
            }
        }

        pureCashValue = tempCash;
        itemValue = tempItemVal;
        totalBankValue = tempCash + tempItemVal;

        panel.updateBankMetrics(totalBankValue, itemValue, pureCashValue);
        
        // Trigger a sync instantly on bank opening to update server records
        syncPlayerProfile();
    }

    @Subscribe
    public void onGrandExchangeOfferChanged(GrandExchangeOfferChanged event)
    {
        if (!config.autoLogTrades())
        {
            return;
        }

        int slot = event.getSlot();
        GrandExchangeOffer offer = event.getOffer();
        GrandExchangeOfferState state = offer.getState();

        GrandExchangeOfferState previousState = previousOfferStates.get(slot);
        previousOfferStates.put(slot, state);

        // We check if slot offer just finished / completed
        if (state == GrandExchangeOfferState.BUY_LIMIT_COMPLETED || 
            state == GrandExchangeOfferState.SELL_LIMIT_COMPLETED)
        {
            if (previousState != state) // avoid multiple triggers per slot completion
            {
                log.info("GE Offer slot {} completed! Logging trade: {}x item ID {}", slot, offer.getTotalQuantity(), offer.getItemId());
                logCompletedTradeToServer(offer, slot);
            }
        }
    }

    private void syncPlayerProfile()
    {
        if (client.getGameState() != GameState.LOGGED_IN || client.getLocalPlayer() == null)
        {
            return;
        }

        String rsn = client.getLocalPlayer().getName();
        Map<String, Integer> skills = new HashMap<>();
        for (Skill skill : Skill.values())
        {
            skills.put(skill.getName(), client.getRealSkillLevel(skill));
        }

        Map<String, Object> payload = new HashMap<>();
        payload.put("rsn", rsn);
        payload.put("skills", skills);
        payload.put("cash_stack", pureCashValue);
        payload.put("bank_value", totalBankValue);

        String json = gson.toJson(payload);
        RequestBody body = RequestBody.create(json, MediaType.parse("application/json; charset=utf-8"));

        Request request = new Request.Builder()
            .url(config.apiUrl() + "/api/runelite/sync")
            .post(body)
            .build();

        okHttpClient.newCall(request).enqueue(new Callback()
        {
            @Override
            public void onFailure(Call call, IOException e)
            {
                log.error("Failed to sync profile with GE Quant server: {}", e.getMessage());
            }

            @Override
            public void onResponse(Call call, Response response) throws IOException
            {
                try (Response r = response)
                {
                    if (r.isSuccessful())
                    {
                        String respJson = r.body().string();
                        // Parse recommendations from response to update panel lists
                        Map<String, Object> respMap = gson.fromJson(respJson, Map.class);
                        if (respMap != null && respMap.containsKey("recommendations"))
                        {
                            panel.updateRecommendations((Map<String, Object>) respMap.get("recommendations"));
                        }
                    }
                    else
                    {
                        log.warn("Server returned unsuccessful sync code: {}", r.code());
                    }
                }
                catch (Exception e)
                {
                    log.error("Error parsing sync response: {}", e.getMessage());
                }
            }
        });
    }

    private void logCompletedTradeToServer(GrandExchangeOffer offer, int slot)
    {
        String rsn = client.getLocalPlayer().getName();
        boolean isBuy = offer.getState() == GrandExchangeOfferState.BUY_LIMIT_COMPLETED;

        Map<String, Object> payload = new HashMap<>();
        payload.put("rsn", rsn);
        payload.put("slot", slot);
        payload.put("item_id", offer.getItemId());
        payload.put("item_name", itemManager.getItemComposition(offer.getItemId()).getName());
        payload.put("qty", offer.getTotalQuantity());
        payload.put("price_each", offer.getSpent() / offer.getTotalQuantity());
        payload.put("side", isBuy ? "buy" : "sell");
        payload.put("state", "COMPLETED");

        String json = gson.toJson(payload);
        RequestBody body = RequestBody.create(json, MediaType.parse("application/json; charset=utf-8"));

        Request request = new Request.Builder()
            .url(config.apiUrl() + "/api/runelite/trade-event")
            .post(body)
            .build();

        okHttpClient.newCall(request).enqueue(new Callback()
        {
            @Override
            public void onFailure(Call call, IOException e)
            {
                log.error("Failed to post trade event to GE Quant server: {}", e.getMessage());
            }

            @Override
            public void onResponse(Call call, Response response) throws IOException
            {
                response.close();
            }
        });
    }
}
