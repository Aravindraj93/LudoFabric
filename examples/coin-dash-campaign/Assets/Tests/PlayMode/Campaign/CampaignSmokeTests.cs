using System.Collections;
using CoinDash.Campaign;
using CoinDash.Core;
using CoinDash.Level;
using LudoFabric.Runtime;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using UnityEngine.UI;

namespace CoinDash.Tests
{
    public sealed class CampaignSmokeTests
    {
        private string previousProfile;
        private CampaignController controller;
        private SceneReferences refs;

        [UnitySetUp] public IEnumerator SetUp()
        {
            previousProfile = CampaignProgress.ProfileId;
            CampaignProgress.ProfileId = "tests." + System.Guid.NewGuid().ToString("N");
            yield return SceneManager.LoadSceneAsync("CoinDashArena");
            yield return null;
            Resolve();
        }
        private void Resolve()
        {
            controller = Object.FindFirstObjectByType<CampaignController>();
            refs = Object.FindFirstObjectByType<SceneReferences>();
            Assert.NotNull(controller); Assert.IsTrue(refs.GameManager.IsInitialized);
        }

        [TearDown] public void TearDown()
        {
            Time.timeScale = 1f;
            if (controller != null)
            {
                var data=controller.Definition;
                for(int i=0;i<data.stages.Length;i++)
                {
                    PlayerPrefs.DeleteKey(CampaignProgress.StageKey(data,i)+".best");
                    PlayerPrefs.DeleteKey(CampaignProgress.StageKey(data,i)+".stars");
                }
                PlayerPrefs.DeleteKey(CampaignProgress.CampaignKey(data)+".unlocked");
                PlayerPrefs.DeleteKey(CampaignProgress.CampaignKey(data)+".selected");
            }
            CampaignProgress.ProfileId=previousProfile;
        }

        [UnityTest] public IEnumerator AllFiveStagesLoadWithDistinctAuthoredLayouts()
        {
            Assert.AreEqual(5, controller.LevelCount);
            Assert.IsFalse(controller.SelectLevel(1), "New profiles must not skip locked stages.");
            for(int stage=0;stage<5;stage++)
            {
                if(stage>0)
                {
                    var data=controller.Definition;
                    Assert.IsTrue(CampaignProgress.Complete(data,stage-1,data.stages[stage-1].goldSeconds));
                    Assert.IsTrue(controller.SelectLevel(stage));
                    yield return null; yield return null;
                    Resolve();
                }
                Assert.AreEqual(stage,controller.CurrentLevelIndex);
                var definition=controller.Definition.stages[stage];
                Assert.AreEqual(definition.seconds,refs.RunTimer.DurationSeconds,.01f);
                Assert.AreEqual(10,refs.Coins.Length);
                for(int coin=0;coin<10;coin++)
                {
                    var actual=refs.Coins[coin].transform.position;
                    Assert.AreEqual(definition.coins[coin].x,actual.x,.01f);
                    Assert.AreEqual(definition.coins[coin].z,actual.z,.01f);
                }
                string capture=System.Environment.GetEnvironmentVariable("LUDOFABRIC_CAPTURE_DIR");
                if(!string.IsNullOrEmpty(capture))
                {
                    refs.GameManager.StartGame(); refs.GameManager.DismissTutorial();
                    yield return new WaitForSecondsRealtime(.15f);
                    System.IO.Directory.CreateDirectory(capture);
                    CaptureFrame(System.IO.Path.Combine(capture,"stage-"+(stage+1)+".png"));
                    yield return new WaitForSecondsRealtime(.3f);
                }
            }
        }

        [UnityTest] public IEnumerator LevelMenuPausesAndResumesTheRun()
        {
            refs.GameManager.StartGame(); refs.GameManager.DismissTutorial();
            Assert.AreEqual(GameState.Playing,refs.GameManager.CurrentState);
            yield return new WaitForSeconds(.15f);
            Button menu=null,close=null;
            foreach(var button in controller.GetComponentsInChildren<Button>(true))
            {
                if(button.name=="Levels") menu=button;
                if(button.name=="Close") close=button;
            }
            Assert.NotNull(menu); Assert.NotNull(close);
            menu.onClick.Invoke();
            Assert.AreEqual(GameState.Paused,refs.GameManager.CurrentState);
            float remaining=refs.RunTimer.RemainingSeconds;
            yield return new WaitForSecondsRealtime(.15f);
            string capture=System.Environment.GetEnvironmentVariable("LUDOFABRIC_CAPTURE_DIR");
            if(!string.IsNullOrEmpty(capture))
            {
                CaptureFrame(System.IO.Path.Combine(capture,"level-menu.png"));
                yield return new WaitForSecondsRealtime(.3f);
            }
            Assert.AreEqual(remaining,refs.RunTimer.RemainingSeconds,.001f);
            close.onClick.Invoke();
            Assert.AreEqual(GameState.Playing,refs.GameManager.CurrentState);
        }

        [UnityTest] public IEnumerator SlidingGatesMoveDuringPlayAndFreezeOnPause()
        {
            for(int i=0;i<3;i++) Assert.IsTrue(CampaignProgress.Complete(controller.Definition,i,20));
            Assert.IsTrue(controller.SelectLevel(3));
            yield return null; yield return null; Resolve();
            var gates=Object.FindObjectsByType<MovingGate>(FindObjectsSortMode.None);
            Assert.Greater(gates.Length,0);
            refs.GameManager.StartGame(); refs.GameManager.DismissTutorial();
            Vector3 initial=gates[0].transform.position;
            yield return new WaitForSeconds(.3f);
            Assert.Greater((gates[0].transform.position-initial).sqrMagnitude,.01f);
            refs.GameManager.Pause();
            Vector3 paused=gates[0].transform.position;
            yield return new WaitForSecondsRealtime(.2f);
            Assert.Less((gates[0].transform.position-paused).sqrMagnitude,.0001f);
        }

        private void CaptureFrame(string path)
        {
            var camera=refs.MainCamera;
            var target=new RenderTexture(1280,720,24);
            var previousTarget=camera.targetTexture;
            var previousActive=RenderTexture.active;
            var canvases=Object.FindObjectsByType<Canvas>(FindObjectsSortMode.None);
            var modes=new RenderMode[canvases.Length];
            var cameras=new Camera[canvases.Length];
            var distances=new float[canvases.Length];
            Texture2D pixels=null;
            try
            {
                camera.targetTexture=target;
                for(int i=0;i<canvases.Length;i++)
                {
                    modes[i]=canvases[i].renderMode; cameras[i]=canvases[i].worldCamera; distances[i]=canvases[i].planeDistance;
                    if(modes[i]==RenderMode.ScreenSpaceOverlay)
                    {
                        canvases[i].renderMode=RenderMode.ScreenSpaceCamera;
                        canvases[i].worldCamera=camera; canvases[i].planeDistance=1f;
                    }
                }
                Canvas.ForceUpdateCanvases(); camera.Render(); RenderTexture.active=target;
                pixels=new Texture2D(1280,720,TextureFormat.RGB24,false);
                pixels.ReadPixels(new Rect(0,0,1280,720),0,0); pixels.Apply();
                System.IO.File.WriteAllBytes(path,pixels.EncodeToPNG());
            }
            finally
            {
                for(int i=0;i<canvases.Length;i++)
                {
                    canvases[i].renderMode=modes[i]; canvases[i].worldCamera=cameras[i]; canvases[i].planeDistance=distances[i];
                }
                camera.targetTexture=previousTarget; RenderTexture.active=previousActive;
                target.Release(); Object.Destroy(target); if(pixels!=null) Object.Destroy(pixels);
            }
        }
    }
}
