using LudoFabric.Runtime;
using NUnit.Framework;
using UnityEngine;

namespace CoinDash.Tests
{
    public sealed class CampaignProgressTests
    {
        private CampaignDefinition campaign;
        [SetUp] public void SetUp()
        {
            campaign=ScriptableObject.CreateInstance<CampaignDefinition>();
            campaign.campaignId="test."+System.Guid.NewGuid().ToString("N");
            campaign.stages=new[] { Stage("one"), Stage("two") };
        }
        private static StageDefinition Stage(string id) => new StageDefinition { id=id,seconds=60,goldSeconds=20,silverSeconds=40,coins=new[]{Vector3.zero} };
        [TearDown] public void TearDown()
        {
            for(int i=0;i<2;i++) { PlayerPrefs.DeleteKey(CampaignProgress.StageKey(campaign,i)+".best"); PlayerPrefs.DeleteKey(CampaignProgress.StageKey(campaign,i)+".stars"); }
            PlayerPrefs.DeleteKey("LudoFabric."+campaign.campaignId+".unlocked");
            Object.DestroyImmediate(campaign);
        }
        [Test] public void NewCampaignOnlyUnlocksFirstStage() { Assert.AreEqual(0,CampaignProgress.HighestUnlocked(campaign)); }
        [Test] public void VictoryUnlocksNextAndKeepsBetterRecords()
        {
            Assert.IsTrue(CampaignProgress.Complete(campaign,0,18));
            Assert.AreEqual(1,CampaignProgress.HighestUnlocked(campaign));
            Assert.AreEqual(3,CampaignProgress.Stars(campaign,0));
            CampaignProgress.Complete(campaign,0,35);
            Assert.AreEqual(18,CampaignProgress.Best(campaign,0));
            Assert.AreEqual(3,CampaignProgress.Stars(campaign,0));
        }
        [Test] public void LockedOrInvalidCompletionsCannotUnlockProgress()
        {
            Assert.IsFalse(CampaignProgress.Complete(campaign,1,10));
            Assert.IsFalse(CampaignProgress.Complete(campaign,0,float.NaN));
            Assert.IsFalse(CampaignProgress.Complete(campaign,0,61));
            Assert.AreEqual(0,CampaignProgress.HighestUnlocked(campaign));
        }
        [Test] public void RecordsAreIsolatedByStableStageId()
        {
            CampaignProgress.Complete(campaign,0,25);
            Assert.AreEqual(0,CampaignProgress.Best(campaign,1));
            Assert.AreEqual(2,CampaignProgress.Stars(campaign,0));
        }
    }
}
