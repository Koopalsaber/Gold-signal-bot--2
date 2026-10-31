//+------------------------------------------------------------------+
//| Ahuramazda EURCHF EA - Prototype v0.2                            |
//| DEMO TEST ONLY                                                    |
//+------------------------------------------------------------------+
#property strict
#property version "0.20"

#include <Trade/Trade.mqh>
CTrade trade;

input string InpSymbol="EURCHF";
input bool InpEnableTrading=false;
input ulong InpMagic=26103101;

input double InpRiskPercent=0.25;
input double InpMaxOpenRiskPercent=1.0;
input double InpDailyLossLimitPercent=2.0;
input double InpMaxDrawdownPercent=5.0;
input double InpRewardRisk=2.0;

input int InpMinScore=65;
input int InpMaxSpreadPoints=35;
input int InpSwingWing=3;

input int InpATRPeriod=14;
input int InpRSIPeriod=14;
input int InpADXPeriod=14;
input int InpFastEMA=20;
input int InpSlowEMA=50;

input bool InpUseH4Context=true;
input bool InpUseM15Confirmation=true;
input bool InpRequireHedging=true;
input bool InpAllowOppositePositions=true;
input bool InpDiagnostics=true;

int hFast1=INVALID_HANDLE;
int hSlow1=INVALID_HANDLE;
int hFast4=INVALID_HANDLE;
int hSlow4=INVALID_HANDLE;

int hRSI=INVALID_HANDLE;
int hADX=INVALID_HANDLE;
int hATR=INVALID_HANDLE;
int hMACD=INVALID_HANDLE;
int hBands=INVALID_HANDLE;

datetime lastBar=0;
double initialEquity=0;
double dayStartEquity=0;

int storedDay=-1;
bool riskHalt=false;
string gvPrefix="";
int ScoreRawMax=85;

bool Buf(int h,int b,int shift,double &v)
{
   double a[];
   ArraySetAsSeries(a,true);

   if(h==INVALID_HANDLE ||
      CopyBuffer(h,b,shift,1,a)!=1)
      return false;

   v=a[0];
   return MathIsValidNumber(v);
}

bool Rates(ENUM_TIMEFRAMES tf,int n,MqlRates &r[])
{
   ArraySetAsSeries(r,true);
   return CopyRates(InpSymbol,tf,0,n,r)==n;
}

double NPrice(double p)
{
   return NormalizeDouble(
      p,
      (int)SymbolInfoInteger(InpSymbol,SYMBOL_DIGITS)
   );
}

bool NewH1Bar()
{
   datetime t=iTime(InpSymbol,PERIOD_H1,0);

   if(t<=0) return false;

   if(t!=lastBar)
   {
      lastBar=t;
      return true;
   }

   return false;
}int DateKey()
{
   MqlDateTime d;
   TimeToStruct(TimeCurrent(),d);
   return d.year*10000+d.mon*100+d.day;
}

void UpdateDay()
{
   int key=DateKey();
   double eq=AccountInfoDouble(ACCOUNT_EQUITY);

   if(key!=storedDay)
   {
      storedDay=key;
      dayStartEquity=eq;

      GlobalVariableSet(gvPrefix+"_DAY",(double)storedDay);
      GlobalVariableSet(gvPrefix+"_DAYEQ",dayStartEquity);
      GlobalVariableSet(gvPrefix+"_DAYHALT",0.0);

      Print("Ahuramazda: new daily baseline ",dayStartEquity);
   }
}

bool Halted()
{
   UpdateDay();

   double eq=AccountInfoDouble(ACCOUNT_EQUITY);

   double dl=dayStartEquity>0 ?
      (dayStartEquity-eq)/dayStartEquity*100.0 : 0.0;

   double dd=initialEquity>0 ?
      (initialEquity-eq)/initialEquity*100.0 : 0.0;

   if(dl>=InpDailyLossLimitPercent)
      GlobalVariableSet(gvPrefix+"_DAYHALT",1.0);

   if(dd>=InpMaxDrawdownPercent)
      GlobalVariableSet(gvPrefix+"_MAXHALT",1.0);

   bool dayLocked=
      GlobalVariableCheck(gvPrefix+"_DAYHALT") &&
      GlobalVariableGet(gvPrefix+"_DAYHALT")>0.5;

   bool maxLocked=
      GlobalVariableCheck(gvPrefix+"_MAXHALT") &&
      GlobalVariableGet(gvPrefix+"_MAXHALT")>0.5;

   riskHalt=dayLocked || maxLocked;

   return riskHalt;
}

bool Hedging()
{
   return (ENUM_ACCOUNT_MARGIN_MODE)
      AccountInfoInteger(ACCOUNT_MARGIN_MODE)
      ==ACCOUNT_MARGIN_MODE_RETAIL_HEDGING;
}

bool SpreadOK()
{
   MqlTick t;

   if(!SymbolInfoTick(InpSymbol,t))
      return false;

   double pt=SymbolInfoDouble(InpSymbol,SYMBOL_POINT);

   if(pt<=0) return false;

   return ((t.ask-t.bid)/pt<=InpMaxSpreadPoints);
}

bool H4Direction(int &dir)
{
   dir=0;

   double f,s;

   if(!Buf(hFast4,0,1,f) ||
      !Buf(hSlow4,0,1,s))
      return false;

   if(f>s) dir=1;
   else if(f<s) dir=-1;

   return true;
}

bool M15Confirm(int dir)
{
   if(!InpUseM15Confirmation)
      return true;

   MqlRates r[];

   if(!Rates(PERIOD_M15,3,r))
      return false;

   return dir>0 ?
      r[1].close>r[1].open :
      r[1].close<r[1].open;
}

bool Engulfing(int dir)
{
   MqlRates r[];

   if(!Rates(PERIOD_H1,3,r))
      return false;

   double ph=MathMax(r[2].open,r[2].close);
   double pl=MathMin(r[2].open,r[2].close);

   double ch=MathMax(r[1].open,r[1].close);
   double cl=MathMin(r[1].open,r[1].close);

   if(dir>0)
      return r[2].close<r[2].open &&
             r[1].close>r[1].open &&
             ch>=ph && cl<=pl;

   return r[2].close>r[2].open &&
          r[1].close<r[1].open &&
          ch>=ph && cl<=pl;
}

bool RecentSwing(bool buy,double &level)
{
   MqlRates r[];

   int n=100;
   int wing=MathMax(2,InpSwingWing);

   if(!Rates(PERIOD_H1,n,r))
      return false;

   for(int i=wing+1;i<n-wing;i++)
   {
      bool ok=true;
      double x=buy?r[i].low:r[i].high;

      for(int j=1;j<=wing;j++)
      {
         if(buy && (r[i-j].low<=x || r[i+j].low<x))
         {
            ok=false;
            break;
         }

         if(!buy && (r[i-j].high>=x || r[i+j].high>x))
         {
            ok=false;
            break;
         }
      }

      if(ok)
      {
         level=x;
         return true;
      }
   }

   return false;
}int DateKey()
{
   MqlDateTime d;
   TimeToStruct(TimeCurrent(),d);
   return d.year*10000+d.mon*100+d.day;
}

void UpdateDay()
{
   int key=DateKey();
   double eq=AccountInfoDouble(ACCOUNT_EQUITY);

   if(key!=storedDay)
   {
      storedDay=key;
      dayStartEquity=eq;

      GlobalVariableSet(gvPrefix+"_DAY",(double)storedDay);
      GlobalVariableSet(gvPrefix+"_DAYEQ",dayStartEquity);
      GlobalVariableSet(gvPrefix+"_DAYHALT",0.0);

      Print("Ahuramazda: new daily baseline ",dayStartEquity);
   }
}

bool Halted()
{
   UpdateDay();

   double eq=AccountInfoDouble(ACCOUNT_EQUITY);

   double dl=dayStartEquity>0 ?
      (dayStartEquity-eq)/dayStartEquity*100.0 : 0.0;

   double dd=initialEquity>0 ?
      (initialEquity-eq)/initialEquity*100.0 : 0.0;

   if(dl>=InpDailyLossLimitPercent)
      GlobalVariableSet(gvPrefix+"_DAYHALT",1.0);

   if(dd>=InpMaxDrawdownPercent)
      GlobalVariableSet(gvPrefix+"_MAXHALT",1.0);

   bool dayLocked=
      GlobalVariableCheck(gvPrefix+"_DAYHALT") &&
      GlobalVariableGet(gvPrefix+"_DAYHALT")>0.5;

   bool maxLocked=
      GlobalVariableCheck(gvPrefix+"_MAXHALT") &&
      GlobalVariableGet(gvPrefix+"_MAXHALT")>0.5;

   riskHalt=dayLocked || maxLocked;

   return riskHalt;
}

bool Hedging()
{
   return (ENUM_ACCOUNT_MARGIN_MODE)
      AccountInfoInteger(ACCOUNT_MARGIN_MODE)
      ==ACCOUNT_MARGIN_MODE_RETAIL_HEDGING;
}

bool SpreadOK()
{
   MqlTick t;

   if(!SymbolInfoTick(InpSymbol,t))
      return false;

   double pt=SymbolInfoDouble(InpSymbol,SYMBOL_POINT);

   if(pt<=0) return false;

   return ((t.ask-t.bid)/pt<=InpMaxSpreadPoints);
}

bool H4Direction(int &dir)
{
   dir=0;

   double f,s;

   if(!Buf(hFast4,0,1,f) ||
      !Buf(hSlow4,0,1,s))
      return false;

   if(f>s) dir=1;
   else if(f<s) dir=-1;

   return true;
}

bool M15Confirm(int dir)
{
   if(!InpUseM15Confirmation)
      return true;

   MqlRates r[];

   if(!Rates(PERIOD_M15,3,r))
      return false;

   return dir>0 ?
      r[1].close>r[1].open :
      r[1].close<r[1].open;
}

bool Engulfing(int dir)
{
   MqlRates r[];

   if(!Rates(PERIOD_H1,3,r))
      return false;

   double ph=MathMax(r[2].open,r[2].close);
   double pl=MathMin(r[2].open,r[2].close);

   double ch=MathMax(r[1].open,r[1].close);
   double cl=MathMin(r[1].open,r[1].close);

   if(dir>0)
      return r[2].close<r[2].open &&
             r[1].close>r[1].open &&
             ch>=ph && cl<=pl;

   return r[2].close>r[2].open &&
          r[1].close<r[1].open &&
          ch>=ph && cl<=pl;
}

bool RecentSwing(bool buy,double &level)
{
   MqlRates r[];

   int n=100;
   int wing=MathMax(2,InpSwingWing);

   if(!Rates(PERIOD_H1,n,r))
      return false;

   for(int i=wing+1;i<n-wing;i++)
   {
      bool ok=true;
      double x=buy?r[i].low:r[i].high;

      for(int j=1;j<=wing;j++)
      {
         if(buy && (r[i-j].low<=x || r[i+j].low<x))
         {
            ok=false;
            break;
         }

         if(!buy && (r[i-j].high>=x || r[i+j].high>x))
         {
            ok=false;
            break;
         }
      }

      if(ok)
      {
         level=x;
         return true;
      }
   }

   return false;
}int Score(int dir)
{
   double ef,es,rsi,adx,pdi,mdi,atr;
   double macd,sig,up,mid,lo;

   if(!Buf(hFast1,0,1,ef) ||
      !Buf(hSlow1,0,1,es) ||
      !Buf(hRSI,0,1,rsi) ||
      !Buf(hADX,0,1,adx) ||
      !Buf(hADX,1,1,pdi) ||
      !Buf(hADX,2,1,mdi) ||
      !Buf(hATR,0,1,atr) ||
      !Buf(hMACD,0,1,macd) ||
      !Buf(hMACD,1,1,sig) ||
      !Buf(hBands,1,1,up) ||
      !Buf(hBands,0,1,mid) ||
      !Buf(hBands,2,1,lo))
      return 0;

   MqlRates r[];
   if(!Rates(PERIOD_H1,5,r)) return 0;

   double c=r[1].close;
   int s=0;

   if(dir>0 && ef>es) s+=6;
   if(dir<0 && ef<es) s+=6;
   if(dir>0 && c>ef) s+=4;
   if(dir<0 && c<ef) s+=4;

   if(dir>0 && rsi>50) s+=6;
   if(dir<0 && rsi<50) s+=6;
   if(dir>0 && rsi>55) s+=4;
   if(dir<0 && rsi<45) s+=4;

   if(adx>=18) s+=4;
   if(dir>0 && pdi>mdi) s+=6;
   if(dir<0 && mdi>pdi) s+=6;

   if(dir>0 && macd>sig) s+=5;
   if(dir<0 && macd<sig) s+=5;

   if(dir>0 && c>mid) s+=3;
   if(dir<0 && c<mid) s+=3;

   if(up>lo && atr>0) s+=2;

   if(Engulfing(dir)) s+=15;

   double sw=0;

   if(RecentSwing(dir>0,sw))
   {
      if(MathAbs(c-sw)<=atr*1.5)
         s+=10;

      if(dir>0 && c>sw && c>r[2].high)
         s+=10;

      if(dir<0 && c<sw && c<r[2].low)
         s+=10;
   }

   int hd=0;

   if(InpUseH4Context &&
      H4Direction(hd) && hd==dir)
      s+=5;

   if(M15Confirm(dir))
      s+=5;

   return (int)MathRound((double)s*100.0/85.0);
}

// محاسبه حجم بر اساس ریسک واقعی حد ضرر
double VolumeForRisk(ENUM_ORDER_TYPE type,double entry,double sl)
{
   double budget=
      AccountInfoDouble(ACCOUNT_EQUITY)*
      InpRiskPercent/100.0;

   double pl=0;

   if(!OrderCalcProfit(type,InpSymbol,1.0,entry,sl,pl))
      return 0;

   double loss=MathAbs(pl);
   if(loss<=0) return 0;

   double raw=budget/loss;

   double step=SymbolInfoDouble(InpSymbol,SYMBOL_VOLUME_STEP);
   double vmin=SymbolInfoDouble(InpSymbol,SYMBOL_VOLUME_MIN);
   double vmax=SymbolInfoDouble(InpSymbol,SYMBOL_VOLUME_MAX);

   if(step<=0) return 0;

   double v=MathFloor(raw/step)*step;
   v=NormalizeDouble(v,2);

   if(v<vmin)
      return 0;

   return MathMin(v,vmax);
}

// محاسبه ریسک معاملات باز همین نماد و مجیک
double CurrentOpenRisk()
{
   double total=0;

   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(!PositionGetTicket(i)) continue;

      if(PositionGetString(POSITION_SYMBOL)!=InpSymbol ||
         (ulong)PositionGetInteger(POSITION_MAGIC)!=InpMagic)
         continue;

      double sl=PositionGetDouble(POSITION_SL);
      double vol=PositionGetDouble(POSITION_VOLUME);
      double open=PositionGetDouble(POSITION_PRICE_OPEN);

      if(sl<=0) return DBL_MAX;

      ENUM_POSITION_TYPE p=
         (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

      ENUM_ORDER_TYPE ot=
         p==POSITION_TYPE_BUY ?
         ORDER_TYPE_BUY : ORDER_TYPE_SELL;

      double pl=0;

      if(!OrderCalcProfit(ot,InpSymbol,vol,open,sl,pl))
         return DBL_MAX;

      total+=MathAbs(pl);
   }

   return total;
}

bool SameDirectionExists(int dir)
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(!PositionGetTicket(i)) continue;

      if(PositionGetString(POSITION_SYMBOL)!=InpSymbol ||
         (ulong)PositionGetInteger(POSITION_MAGIC)!=InpMagic)
         continue;

      ENUM_POSITION_TYPE p=
         (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

      if((dir>0 && p==POSITION_TYPE_BUY) ||
         (dir<0 && p==POSITION_TYPE_SELL))
         return true;
   }

   return false;
}

bool OppositePositionExists(int dir)
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(!PositionGetTicket(i)) continue;

      if(PositionGetString(POSITION_SYMBOL)!=InpSymbol ||
         (ulong)PositionGetInteger(POSITION_MAGIC)!=InpMagic)
         continue;

      ENUM_POSITION_TYPE p=
         (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

      if((dir>0 && p==POSITION_TYPE_SELL) ||
         (dir<0 && p==POSITION_TYPE_BUY))
         return true;
   }

   return false;
}

// ارسال سفارش
bool SendEntry(int dir)
{
   if(!InpEnableTrading || Halted() || !SpreadOK())
      return false;

   if(InpRequireHedging && !Hedging())
   {
      Print("Rejected: account is not Hedging mode");
      return false;
   }

   if(SameDirectionExists(dir))
      return false;

   if(!InpAllowOppositePositions &&
      OppositePositionExists(dir))
      return false;

   MqlTick t;

   if(!SymbolInfoTick(InpSymbol,t))
      return false;

   double atr=0,sw=0;

   if(!Buf(hATR,0,1,atr) ||
      atr<=0 ||
      !RecentSwing(dir>0,sw))
      return false;

   double entry=dir>0?t.ask:t.bid;

   double sl=dir>0 ?
      NPrice(sw-atr*0.25) :
      NPrice(sw+atr*0.25);

   if((dir>0 && sl>=entry) ||
      (dir<0 && sl<=entry))
      return false;

   double tp=NPrice(
      dir>0 ?
      entry+(entry-sl)*InpRewardRisk :
      entry-(sl-entry)*InpRewardRisk
   );

   double volume=VolumeForRisk(
      dir>0?ORDER_TYPE_BUY:ORDER_TYPE_SELL,
      entry,sl
   );

   if(volume<=0)
   {
      Print("Rejected: invalid volume");
      return false;
   }

   double risk=0;

   if(!OrderCalcProfit(
      dir>0?ORDER_TYPE_BUY:ORDER_TYPE_SELL,
      InpSymbol,volume,entry,sl,risk))
      return false;

   double maxRisk=
      AccountInfoDouble(ACCOUNT_EQUITY)*
      InpMaxOpenRiskPercent/100.0;

   if(CurrentOpenRisk()+MathAbs(risk)>maxRisk)
   {
      Print("Rejected: maximum open risk reached");
      return false;
   }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(20);

   bool sent=false;

   if(dir>0)
      sent=trade.Buy(volume,InpSymbol,0,sl,tp,"Ahuramazda BUY");
   else
      sent=trade.Sell(volume,InpSymbol,0,sl,tp,"Ahuramazda SELL");

   Print("Order result: ",sent,
         " | Retcode: ",trade.ResultRetcode(),
         " | ",trade.ResultRetcodeDescription());

   return sent;
}

// ارزیابی سیگنال
void Evaluate()
{
   if(Halted())
      return;

   int buy=Score(1);
   int sell=Score(-1);

   if(InpDiagnostics)
      Print("Ahuramazda scores | BUY=",buy,
            " SELL=",sell);

   if(buy>=InpMinScore && buy>sell)
      SendEntry(1);

   else if(sell>=InpMinScore && sell>buy)
      SendEntry(-1);
}

// راه‌اندازی
int OnInit()
{
   if(!SymbolSelect(InpSymbol,true))
      return INIT_FAILED;

   gvPrefix="AHURA_"+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))
            +"_"+InpSymbol+"_"+IntegerToString((long)InpMagic);

   if(GlobalVariableCheck(gvPrefix+"_INITIAL"))
      initialEquity=GlobalVariableGet(gvPrefix+"_INITIAL");
   else
   {
      initialEquity=AccountInfoDouble(ACCOUNT_EQUITY);
      GlobalVariableSet(gvPrefix+"_INITIAL",initialEquity);
   }

   if(GlobalVariableCheck(gvPrefix+"_DAY"))
      storedDay=(int)GlobalVariableGet(gvPrefix+"_DAY");

   if(GlobalVariableCheck(gvPrefix+"_DAYEQ"))
      dayStartEquity=GlobalVariableGet(gvPrefix+"_DAYEQ");

   hFast1=iMA(InpSymbol,PERIOD_H1,InpFastEMA,0,MODE_EMA,PRICE_CLOSE);
   hSlow1=iMA(InpSymbol,PERIOD_H1,InpSlowEMA,0,MODE_EMA,PRICE_CLOSE);

   hFast4=iMA(InpSymbol,PERIOD_H4,InpFastEMA,0,MODE_EMA,PRICE_CLOSE);
   hSlow4=iMA(InpSymbol,PERIOD_H4,InpSlowEMA,0,MODE_EMA,PRICE_CLOSE);

   hRSI=iRSI(InpSymbol,PERIOD_H1,InpRSIPeriod,PRICE_CLOSE);
   hADX=iADX(InpSymbol,PERIOD_H1,InpADXPeriod);
   hATR=iATR(InpSymbol,PERIOD_H1,InpATRPeriod);
   hMACD=iMACD(InpSymbol,PERIOD_H1,12,26,9,PRICE_CLOSE);
   hBands=iBands(InpSymbol,PERIOD_H1,20,0,2.0,PRICE_CLOSE);

   if(hFast1==INVALID_HANDLE ||
      hSlow1==INVALID_HANDLE ||
      hFast4==INVALID_HANDLE ||
      hSlow4==INVALID_HANDLE ||
      hRSI==INVALID_HANDLE ||
      hADX==INVALID_HANDLE ||
      hATR==INVALID_HANDLE ||
      hMACD==INVALID_HANDLE ||
      hBands==INVALID_HANDLE)
      return INIT_FAILED;

   trade.SetExpertMagicNumber(InpMagic);

   Print("Ahuramazda v0.20 initialized. Trading enabled: ",
         InpEnableTrading);

   return INIT_SUCCEEDED;
}

// پایان کار
void OnDeinit(const int reason)
{
   IndicatorRelease(hFast1);
   IndicatorRelease(hSlow1);
   IndicatorRelease(hFast4);
   IndicatorRelease(hSlow4);
   IndicatorRelease(hRSI);
   IndicatorRelease(hADX);
   IndicatorRelease(hATR);
   IndicatorRelease(hMACD);
   IndicatorRelease(hBands);
}

// اجرای اصلی
void OnTick()
{
   if(_Symbol!=InpSymbol)
      return;

   Halted();

   if(NewH1Bar())
      Evaluate();
}
//+------------------------------------------------------------------+
