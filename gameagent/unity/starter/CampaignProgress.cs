using UnityEngine;

namespace LudoFabric.Runtime
{
    public static class CampaignProgress
    {
        public static string ProfileId { get; set; } = "";
        public static string CampaignKey(CampaignDefinition campaign)
            => "LudoFabric." + campaign.campaignId + (string.IsNullOrEmpty(ProfileId) ? "" : "." + ProfileId);
        public static string StageKey(CampaignDefinition campaign, int index)
            => CampaignKey(campaign) + "." + campaign.stages[index].id;

        public static int HighestUnlocked(CampaignDefinition campaign)
            => Mathf.Clamp(PlayerPrefs.GetInt(CampaignKey(campaign) + ".unlocked", 0), 0, campaign.stages.Length - 1);

        public static int Stars(CampaignDefinition campaign, int index)
            => Mathf.Clamp(PlayerPrefs.GetInt(StageKey(campaign, index) + ".stars", 0), 0, 3);

        public static float Best(CampaignDefinition campaign, int index)
        {
            float value = PlayerPrefs.GetFloat(StageKey(campaign, index) + ".best", 0f);
            return float.IsNaN(value) || float.IsInfinity(value) || value <= 0f ? 0f : value;
        }

        public static bool Complete(CampaignDefinition campaign, int index, float elapsed)
        {
            if (index < 0 || index >= campaign.stages.Length || index > HighestUnlocked(campaign)) return false;
            int stars = campaign.stages[index].Medal(elapsed);
            if (stars == 0 || elapsed > campaign.stages[index].seconds) return false;
            string key = StageKey(campaign, index);
            float previous = Best(campaign, index);
            if (previous == 0f || elapsed < previous) PlayerPrefs.SetFloat(key + ".best", elapsed);
            PlayerPrefs.SetInt(key + ".stars", Mathf.Max(stars, Stars(campaign, index)));
            PlayerPrefs.SetInt(CampaignKey(campaign) + ".unlocked",
                Mathf.Max(HighestUnlocked(campaign), Mathf.Min(index + 1, campaign.stages.Length - 1)));
            PlayerPrefs.Save();
            return true;
        }
    }
}
