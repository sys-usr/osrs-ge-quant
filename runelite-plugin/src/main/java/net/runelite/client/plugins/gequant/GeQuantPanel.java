package net.runelite.client.plugins.gequant;

import java.awt.BorderLayout;
import java.awt.Color;
import java.awt.Dimension;
import java.awt.Font;
import java.awt.GridLayout;
import java.awt.Toolkit;
import java.awt.datatransfer.Clipboard;
import java.awt.datatransfer.StringSelection;
import java.awt.event.MouseAdapter;
import java.awt.event.MouseEvent;
import java.util.List;
import java.util.Map;
import javax.swing.BoxLayout;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.JSeparator;
import javax.swing.SwingUtilities;
import javax.swing.border.EmptyBorder;
import net.runelite.client.ui.ColorScheme;
import net.runelite.client.ui.PluginPanel;
import net.runelite.client.util.QuantityFormatter;

public class GeQuantPanel extends PluginPanel
{
    private final JLabel playerNameText = new JLabel("Offline / Unknown");
    private final JLabel totalValText = new JLabel("0 gp");
    private final JLabel cashValText = new JLabel("0 gp");
    private final JLabel itemValText = new JLabel("0 gp");

    private final JPanel flipListContainer = new JPanel();
    private final JPanel recipeListContainer = new JPanel();

    private GeQuantPlugin plugin;

    public void init(GeQuantPlugin plugin)
    {
        this.plugin = plugin;
        
        setLayout(new BorderLayout());
        setBorder(new EmptyBorder(10, 10, 10, 10));
        setBackground(ColorScheme.DARK_GRAY_COLOR);

        // 1. HEADER / METRICS PANEL
        JPanel headerPanel = new JPanel();
        headerPanel.setLayout(new BoxLayout(headerPanel, BoxLayout.Y_AXIS));
        headerPanel.setBorder(new EmptyBorder(0, 0, 10, 0));
        headerPanel.setBackground(ColorScheme.DARK_GRAY_COLOR);

        JLabel title = new JLabel("GE QUANT TERMINAL");
        title.setFont(new Font(Font.SANS_SERIF, Font.BOLD, 15));
        title.setForeground(ColorScheme.GOLD_COLOR);
        title.setAlignmentX(CENTER_ALIGNMENT);
        headerPanel.add(title);

        playerNameText.setFont(new Font(Font.MONOSPACED, Font.PLAIN, 10));
        playerNameText.setForeground(Color.GRAY);
        playerNameText.setAlignmentX(CENTER_ALIGNMENT);
        headerPanel.add(playerNameText);

        headerPanel.add(new JSeparator());

        // Net Worth overview
        JPanel statsPanel = new JPanel(new GridLayout(3, 2, 5, 5));
        statsPanel.setBackground(ColorScheme.DARKER_GRAY_COLOR);
        statsPanel.setBorder(new EmptyBorder(8, 8, 8, 8));

        statsPanel.add(createLabel("Bank Net Worth:", Color.LIGHT_GRAY, 11));
        totalValText.setFont(new Font(Font.MONOSPACED, Font.BOLD, 11));
        totalValText.setForeground(ColorScheme.GOLD_COLOR);
        statsPanel.add(totalValText);

        statsPanel.add(createLabel("Pure Coins:", Color.LIGHT_GRAY, 10));
        cashValText.setFont(new Font(Font.MONOSPACED, Font.PLAIN, 10));
        cashValText.setForeground(Color.GREEN);
        statsPanel.add(cashValText);

        statsPanel.add(createLabel("Holdings Value:", Color.LIGHT_GRAY, 10));
        itemValText.setFont(new Font(Font.MONOSPACED, Font.PLAIN, 10));
        itemValText.setForeground(Color.CYAN);
        statsPanel.add(itemValText);

        headerPanel.add(statsPanel);
        add(headerPanel, BorderLayout.NORTH);

        // 2. RECOMMENDATIONS CONTAINER
        JPanel contentContainer = new JPanel();
        contentContainer.setLayout(new BoxLayout(contentContainer, BoxLayout.Y_AXIS));
        contentContainer.setBackground(ColorScheme.DARK_GRAY_COLOR);

        // Flips Panel
        JLabel flipHeader = new JLabel("DAY-TRADING FLIPS");
        flipHeader.setFont(new Font(Font.SANS_SERIF, Font.BOLD, 12));
        flipHeader.setForeground(ColorScheme.GOLD_COLOR);
        flipHeader.setBorder(new EmptyBorder(10, 0, 5, 0));
        contentContainer.add(flipHeader);

        flipListContainer.setLayout(new BoxLayout(flipListContainer, BoxLayout.Y_AXIS));
        flipListContainer.setBackground(ColorScheme.DARKER_GRAY_COLOR);
        contentContainer.add(flipListContainer);

        // Processing Panel
        JLabel recipeHeader = new JLabel("SKILLING OPPORTUNITIES");
        recipeHeader.setFont(new Font(Font.SANS_SERIF, Font.BOLD, 12));
        recipeHeader.setForeground(ColorScheme.GOLD_COLOR);
        recipeHeader.setBorder(new EmptyBorder(15, 0, 5, 0));
        contentContainer.add(recipeHeader);

        recipeListContainer.setLayout(new BoxLayout(recipeListContainer, BoxLayout.Y_AXIS));
        recipeListContainer.setBackground(ColorScheme.DARKER_GRAY_COLOR);
        contentContainer.add(recipeListContainer);

        JScrollPane scrollPane = new JScrollPane(contentContainer);
        scrollPane.setBackground(ColorScheme.DARK_GRAY_COLOR);
        scrollPane.setBorder(null);
        add(scrollPane, BorderLayout.CENTER);
    }

    public void updatePlayerName(String rsn)
    {
        SwingUtilities.invokeLater(() -> playerNameText.setText("Active: " + rsn));
    }

    public void updateBankMetrics(long totalVal, long itemVal, long cashVal)
    {
        SwingUtilities.invokeLater(() -> {
            totalValText.setText(QuantityFormatter.formatNumber(totalVal) + " gp");
            cashValText.setText(QuantityFormatter.formatNumber(cashVal) + " gp");
            itemValText.setText(QuantityFormatter.formatNumber(itemVal) + " gp");
        });
    }

    public void updateRecommendations(Map<String, Object> recommendations)
    {
        SwingUtilities.invokeLater(() -> {
            flipListContainer.removeAll();
            recipeListContainer.removeAll();

            // Render flips
            if (recommendations.containsKey("flips"))
            {
                List<Map<String, Object>> flips = (List<Map<String, Object>>) recommendations.get("flips");
                if (flips.isEmpty())
                {
                    flipListContainer.add(createPlaceholder("No high margin flips found."));
                }
                else
                {
                    for (Map<String, Object> flip : flips)
                    {
                        flipListContainer.add(createFlipCard(flip));
                    }
                }
            }

            // Render recipes
            if (recommendations.containsKey("processing"))
            {
                List<Map<String, Object>> recipes = (List<Map<String, Object>>) recommendations.get("processing");
                if (recipes.isEmpty())
                {
                    recipeListContainer.add(createPlaceholder("No eligible skilling opportunities."));
                }
                else
                {
                    for (Map<String, Object> recipe : recipes)
                    {
                        recipeListContainer.add(createRecipeCard(recipe));
                    }
                }
            }

            revalidate();
            repaint();
        });
    }

    private JPanel createFlipCard(Map<String, Object> flip)
    {
        JPanel card = new JPanel(new BorderLayout());
        card.setBackground(ColorScheme.DARKER_GRAY_COLOR);
        card.setBorder(new EmptyBorder(6, 6, 6, 6));

        String name = (String) flip.get("name");
        int buyPrice = ((Number) flip.get("buy_price")).intValue();
        int margin = ((Number) flip.get("margin")).intValue();
        int profit = ((Number) flip.get("expected_profit")).intValue();
        int limit = ((Number) flip.get("limit")).intValue();

        JLabel nameLabel = new JLabel(name);
        nameLabel.setFont(new Font(Font.SANS_SERIF, Font.BOLD, 12));
        nameLabel.setForeground(Color.WHITE);
        nameLabel.setToolTipText("Double-click to copy item name to clipboard");
        
        nameLabel.addMouseListener(new MouseAdapter()
        {
            @Override
            public void mouseClicked(MouseEvent e)
            {
                if (e.getClickCount() == 2)
                {
                    StringSelection selection = new StringSelection(name);
                    Clipboard clipboard = Toolkit.getDefaultToolkit().getSystemClipboard();
                    clipboard.setContents(selection, selection);
                }
            }
        });

        JPanel details = new JPanel(new GridLayout(3, 1));
        details.setBackground(ColorScheme.DARKER_GRAY_COLOR);
        details.add(createLabel("Buy target: " + QuantityFormatter.formatNumber(buyPrice) + " gp", Color.LIGHT_GRAY, 10));
        details.add(createLabel("Margin: +" + QuantityFormatter.formatNumber(margin) + " gp", Color.GRAY, 9));
        details.add(createLabel("Profit: +" + QuantityFormatter.formatNumber(profit) + " gp (Limit: " + limit + ")", Color.GREEN, 9));

        card.add(nameLabel, BorderLayout.NORTH);
        card.add(details, BorderLayout.CENTER);
        
        // Add a line divider
        card.add(new JSeparator(), BorderLayout.SOUTH);

        return card;
    }

    private JPanel createRecipeCard(Map<String, Object> recipe)
    {
        JPanel card = new JPanel(new BorderLayout());
        card.setBackground(ColorScheme.DARKER_GRAY_COLOR);
        card.setBorder(new EmptyBorder(6, 6, 6, 6));

        String recipeName = (String) recipe.get("recipe");
        String skill = (String) recipe.get("skill");
        int level = ((Number) recipe.get("level")).intValue();
        int gpPerBatch = ((Number) recipe.get("gp_per_batch")).intValue();

        JLabel nameLabel = new JLabel(recipeName);
        nameLabel.setFont(new Font(Font.SANS_SERIF, Font.BOLD, 11));
        nameLabel.setForeground(Color.WHITE);

        JPanel details = new JPanel(new GridLayout(2, 1));
        details.setBackground(ColorScheme.DARKER_GRAY_COLOR);
        details.add(createLabel("Skill: " + skill + " (Lvl " + level + ")", Color.LIGHT_GRAY, 9));
        details.add(createLabel("Profit/Batch: +" + QuantityFormatter.formatNumber(gpPerBatch) + " gp", Color.GREEN, 9));

        card.add(nameLabel, BorderLayout.NORTH);
        card.add(details, BorderLayout.CENTER);
        card.add(new JSeparator(), BorderLayout.SOUTH);

        return card;
    }

    private JLabel createLabel(String text, Color color, int size)
    {
        JLabel label = new JLabel(text);
        label.setFont(new Font(Font.SANS_SERIF, Font.PLAIN, size));
        label.setForeground(color);
        return label;
    }

    private JLabel createPlaceholder(String text)
    {
        JLabel label = new JLabel(text);
        label.setFont(new Font(Font.SANS_SERIF, Font.ITALIC, 10));
        label.setForeground(Color.GRAY);
        label.setBorder(new EmptyBorder(5, 5, 5, 5));
        return label;
    }
}
