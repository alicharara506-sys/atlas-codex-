#property copyright "ATLAS Quant"
#property version   "1.00"
#property strict
#property description "Read-only local market-data exporter for ATLAS Quant."
#property description "Exports quotes, closed bars, ticks, and account mode only."

input int InpRefreshSeconds = 2;
input int InpClosedBars = 300;
input int InpRecentTicks = 1000;

string g_atlas_symbols[] =
  {
   "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
   "NZDUSD", "EURGBP", "XAUUSD", "XAGUSD", "USOIL", "UKOIL"
  };

ENUM_TIMEFRAMES g_timeframes[] = {PERIOD_M15, PERIOD_H1, PERIOD_H4, PERIOD_D1};
string g_timeframe_names[] = {"15M", "1H", "4H", "D1"};
int g_cycle = 0;

string Number(const double value)
  {
   return DoubleToString(value, 16);
  }

string AccountMode()
  {
   ENUM_ACCOUNT_TRADE_MODE mode = (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(mode == ACCOUNT_TRADE_MODE_DEMO)
      return "DEMO";
   if(mode == ACCOUNT_TRADE_MODE_CONTEST)
      return "CONTEST";
   return "REAL";
  }

bool Publish(const string temporary_name, const string final_name)
  {
   ResetLastError();
   if(FileMove(temporary_name, 0, final_name, FILE_REWRITE))
      return true;
   PrintFormat("ATLAS bridge could not publish %s (error %d)", final_name, GetLastError());
   return false;
  }

string ResolveBrokerSymbol(const string atlas_symbol)
  {
   string candidates[];
   ArrayResize(candidates, 0);

   if(atlas_symbol == "USOIL")
     {
      ArrayResize(candidates, 2);
      candidates[0] = "WTI";
      candidates[1] = "USOIL";
     }
   else if(atlas_symbol == "UKOIL")
     {
      ArrayResize(candidates, 2);
      candidates[0] = "BRENT";
      candidates[1] = "UKOIL";
     }
   else
     {
      ArrayResize(candidates, 1);
      candidates[0] = atlas_symbol;
     }

   string suffixes[] = {"", ".a", ".m", ".raw", ".pro", "m", "_i", "-ECN"};
   for(int c = 0; c < ArraySize(candidates); c++)
     {
      for(int s = 0; s < ArraySize(suffixes); s++)
        {
         string candidate = candidates[c] + suffixes[s];
         if(SymbolSelect(candidate, true))
            return candidate;
        }
     }
   return "";
  }

void WriteStatus()
  {
   string final_name = "AtlasBridge\\status.csv";
   string temp_name = final_name + ".tmp";
   int handle = FileOpen(temp_name, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
     {
      PrintFormat("ATLAS status open failed (error %d)", GetLastError());
      return;
     }

   FileWrite(handle, "schema", "atlas-mt5-file-v1");
   FileWrite(handle, "generated", (long)TimeGMT());
   // MT5 rates/ticks use broker-server wall-clock timestamps. Record the
   // current broker-to-UTC offset so the local bridge can normalize every
   // timestamp before it crosses the ATLAS data boundary.
   FileWrite(handle, "serverUtcOffsetSeconds", (long)(TimeTradeServer() - TimeGMT()));
   FileWrite(handle, "connected", (int)TerminalInfoInteger(TERMINAL_CONNECTED));
   FileWrite(handle, "build", (long)TerminalInfoInteger(TERMINAL_BUILD));
   FileWrite(handle, "company", TerminalInfoString(TERMINAL_COMPANY));
   FileWrite(handle, "server", AccountInfoString(ACCOUNT_SERVER));
   FileWrite(handle, "currency", AccountInfoString(ACCOUNT_CURRENCY));
   FileWrite(handle, "balance", Number(AccountInfoDouble(ACCOUNT_BALANCE)));
   FileWrite(handle, "equity", Number(AccountInfoDouble(ACCOUNT_EQUITY)));
   FileWrite(handle, "margin", Number(AccountInfoDouble(ACCOUNT_MARGIN)));
   FileWrite(handle, "marginFree", Number(AccountInfoDouble(ACCOUNT_MARGIN_FREE)));
   FileWrite(handle, "leverage", (long)AccountInfoInteger(ACCOUNT_LEVERAGE));
   FileWrite(handle, "tradeMode", AccountMode());
   FileWrite(handle, "tradeAllowed", (int)AccountInfoInteger(ACCOUNT_TRADE_ALLOWED));
   FileWrite(handle, "readOnly", 1);
   FileWrite(handle, "orderSubmission", 0);
   FileFlush(handle);
   FileClose(handle);
   Publish(temp_name, final_name);
  }

void WriteSymbol(const string atlas_symbol, const string broker_symbol)
  {
   MqlTick tick;
   if(!SymbolInfoTick(broker_symbol, tick))
      return;

   string final_name = "AtlasBridge\\symbols\\" + atlas_symbol + ".csv";
   string temp_name = final_name + ".tmp";
   int handle = FileOpen(temp_name, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
      return;

   FileWrite(handle, "symbol", atlas_symbol);
   FileWrite(handle, "brokerSymbol", broker_symbol);
   FileWrite(handle, "digits", (long)SymbolInfoInteger(broker_symbol, SYMBOL_DIGITS));
   FileWrite(handle, "tickSize", Number(SymbolInfoDouble(broker_symbol, SYMBOL_TRADE_TICK_SIZE)));
   FileWrite(handle, "tickValue", Number(SymbolInfoDouble(broker_symbol, SYMBOL_TRADE_TICK_VALUE)));
   FileWrite(handle, "contractSize", Number(SymbolInfoDouble(broker_symbol, SYMBOL_TRADE_CONTRACT_SIZE)));
   FileWrite(handle, "minVolume", Number(SymbolInfoDouble(broker_symbol, SYMBOL_VOLUME_MIN)));
   FileWrite(handle, "maxVolume", Number(SymbolInfoDouble(broker_symbol, SYMBOL_VOLUME_MAX)));
   FileWrite(handle, "lotStep", Number(SymbolInfoDouble(broker_symbol, SYMBOL_VOLUME_STEP)));
   FileWrite(handle, "profitCurrency", SymbolInfoString(broker_symbol, SYMBOL_CURRENCY_PROFIT));
   FileWrite(handle, "marginCurrency", SymbolInfoString(broker_symbol, SYMBOL_CURRENCY_MARGIN));
   FileWrite(handle, "bid", Number(tick.bid));
   FileWrite(handle, "ask", Number(tick.ask));
   FileWrite(handle, "time", (long)tick.time);
   FileFlush(handle);
   FileClose(handle);
   Publish(temp_name, final_name);
  }

void WriteRates(const string atlas_symbol, const string broker_symbol,
                const ENUM_TIMEFRAMES timeframe, const string timeframe_name)
  {
   MqlRates rates[];
   ResetLastError();
   int copied = CopyRates(broker_symbol, timeframe, 1, InpClosedBars, rates);
   if(copied <= 0)
      return;

   string final_name = "AtlasBridge\\rates\\" + atlas_symbol + "_" + timeframe_name + ".csv";
   string temp_name = final_name + ".tmp";
   int handle = FileOpen(temp_name, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
      return;

   FileWrite(handle, "time", "open", "high", "low", "close", "volume");
   for(int i = 0; i < copied; i++)
     {
      FileWrite(handle,
                (long)rates[i].time,
                Number(rates[i].open),
                Number(rates[i].high),
                Number(rates[i].low),
                Number(rates[i].close),
                (long)rates[i].tick_volume);
     }
   FileFlush(handle);
   FileClose(handle);
   Publish(temp_name, final_name);
  }

void WriteTicks(const string atlas_symbol, const string broker_symbol)
  {
   MqlTick ticks[];
   int copied = CopyTicks(broker_symbol, ticks, COPY_TICKS_ALL, 0, InpRecentTicks);
   if(copied <= 0)
      return;

   string final_name = "AtlasBridge\\ticks\\" + atlas_symbol + ".csv";
   string temp_name = final_name + ".tmp";
   int handle = FileOpen(temp_name, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
      return;

   FileWrite(handle, "time", "bid", "ask", "last", "volume");
   for(int i = 0; i < copied; i++)
     {
      double volume = ticks[i].volume_real > 0.0 ? ticks[i].volume_real : (double)ticks[i].volume;
      double seconds = (double)ticks[i].time_msc / 1000.0;
      FileWrite(handle,
                DoubleToString(seconds, 3),
                Number(ticks[i].bid),
                Number(ticks[i].ask),
                Number(ticks[i].last),
                Number(volume));
     }
   FileFlush(handle);
   FileClose(handle);
   Publish(temp_name, final_name);
  }

void ExportData(const bool include_all_history)
  {
   WriteStatus();
   int symbol_count = ArraySize(g_atlas_symbols);
   for(int i = 0; i < symbol_count; i++)
     {
      string atlas_symbol = g_atlas_symbols[i];
      string broker_symbol = ResolveBrokerSymbol(atlas_symbol);
      if(broker_symbol == "")
         continue;

      WriteSymbol(atlas_symbol, broker_symbol);
      if(include_all_history)
        {
         for(int tf = 0; tf < ArraySize(g_timeframes); tf++)
            WriteRates(atlas_symbol, broker_symbol, g_timeframes[tf], g_timeframe_names[tf]);
        }
     }

   if(symbol_count > 0)
     {
      int tick_index = g_cycle % symbol_count;
      string atlas_symbol = g_atlas_symbols[tick_index];
      string broker_symbol = ResolveBrokerSymbol(atlas_symbol);
      if(broker_symbol != "")
         WriteTicks(atlas_symbol, broker_symbol);
     }
  }

int OnInit()
  {
   if(InpRefreshSeconds < 1 || InpClosedBars < 60 || InpRecentTicks < 1)
      return INIT_PARAMETERS_INCORRECT;
   if(!EventSetTimer(InpRefreshSeconds))
      return INIT_FAILED;
   ExportData(true);
   Print("ATLAS read-only market-data bridge started. No trading functions are present.");
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   PrintFormat("ATLAS read-only market-data bridge stopped (reason %d).", reason);
  }

void OnTimer()
  {
   g_cycle++;
   bool refresh_history = (g_cycle % 15 == 0);
   ExportData(refresh_history);
  }
