export type TerminalSnapshot = {
  meta: {
    top_items: number;
    generator: string;
  };
  accounts: Record<string, unknown>[];
  account_pnl: Record<string, unknown>[];
  recommendations: Record<string, unknown>[];
  liquid_items: Record<string, unknown>[];
};
