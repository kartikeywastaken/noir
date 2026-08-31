namespace Game.Economy;

public static class CurrencyManager
{
    public const int StartingCoins = 5;

    public static bool CanAfford(int amount)
    {
        return amount <= StartingCoins;
    }

    public static int Untouched()
    {
        return 7;
    }
}
