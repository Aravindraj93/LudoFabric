using CoinDash.Core;
using CoinDash.Level;
using CoinDash.UI;
using LudoFabric.Runtime;
using TMPro;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UI;

namespace CoinDash.Campaign
{
    [DefaultExecutionOrder(-1000)]
    public sealed class CampaignController : MonoBehaviour
    {
        [SerializeField] private CampaignDefinition campaign;
        [SerializeField] private SceneReferences references;
        private GameConfig runConfig;
        private MovingGate[] gates;
        private TMP_Text heading;
        private GameObject selector;
        private Button next;
        private Button[] stageButtons;
        private bool resumeAfterMenu;
        public int CurrentLevelIndex { get; private set; }
        public int LevelCount => campaign.stages.Length;
        public CampaignDefinition Definition => campaign;
        private string SelectionKey => CampaignProgress.CampaignKey(campaign) + ".selected";

        public void Configure(CampaignDefinition data, SceneReferences refs) { campaign = data; references = refs; }

        private void Awake()
        {
            campaign.Validate();
            CurrentLevelIndex = Mathf.Clamp(PlayerPrefs.GetInt(SelectionKey, 0), 0, CampaignProgress.HighestUnlocked(campaign));
            StageDefinition stage = campaign.stages[CurrentLevelIndex];
            if (stage.coins.Length != references.Coins.Length)
                throw new System.InvalidOperationException("Stage coin count must match the scene's collectible pool.");
            runConfig = Instantiate(references.Config);
            runConfig.ConfigureLevel(stage.seconds, stage.coins.Length);
            references.Bootstrapper.OverrideConfig(runConfig);
            references.BestTimeStore.ConfigureKey(CampaignProgress.StageKey(campaign, CurrentLevelIndex) + ".best");
            references.PlayerBody.position = stage.spawn;
            references.PlayerBody.transform.position = stage.spawn;
            references.PlayerSpawnData.SetSpawnPose(stage.spawn, Quaternion.identity);
            for (int i = 0; i < stage.coins.Length; i++) references.Coins[i].transform.position = stage.coins[i];
            gates = new MovingGate[stage.obstacles.Length];
            Material material = references.Obstacles[0].GetComponent<Renderer>().sharedMaterial;
            for (int i = 0; i < stage.obstacles.Length; i++)
            {
                var spec = stage.obstacles[i];
                GameObject obstacle;
                if (i < references.Obstacles.Length) obstacle = references.Obstacles[i];
                else
                {
                    obstacle = GameObject.CreatePrimitive(PrimitiveType.Cube);
                    obstacle.name = "CampaignGate_" + i;
                    obstacle.transform.SetParent(references.ObstaclesRoot, false);
                    obstacle.GetComponent<Renderer>().sharedMaterial = material;
                }
                obstacle.isStatic = false;
                obstacle.transform.SetPositionAndRotation(spec.position, Quaternion.Euler(0f, spec.yaw, 0f));
                obstacle.transform.localScale = spec.size;
                if (spec.motion.sqrMagnitude > 0f)
                {
                    gates[i] = obstacle.AddComponent<MovingGate>();
                    gates[i].Configure(references.GameManager, spec.motion, spec.speed);
                }
            }
        }

        private void OnEnable()
        {
            references.Events.RunWon += HandleWin;
            references.Events.StateChanged += HandleState;
            references.Events.RunReset += ResetGates;
        }

        private void OnDisable()
        {
            references.Events.RunWon -= HandleWin;
            references.Events.StateChanged -= HandleState;
            references.Events.RunReset -= ResetGates;
        }

        private void Start()
        {
            BuildHud();
            var block = new MaterialPropertyBlock();
            block.SetColor("_BaseColor", campaign.stages[CurrentLevelIndex].floorColor);
            block.SetTexture("_BaseMap",Texture2D.whiteTexture);
            var floorRenderer=references.Floor.GetComponent<Renderer>();
            floorRenderer.SetPropertyBlock(block);
            for(int slot=0;slot<floorRenderer.sharedMaterials.Length;slot++) floorRenderer.SetPropertyBlock(block,slot);
            HandleState(GameState.Title, references.GameManager.CurrentState);
        }

        private void OnDestroy() { if (runConfig != null) Destroy(runConfig); }
        private void ResetGates() { foreach (var gate in gates) if (gate != null) gate.ResetMotion(); }
        private void HandleWin(RunResult result)
        {
            if (result.Won) CampaignProgress.Complete(campaign, CurrentLevelIndex, result.ElapsedSeconds);
            RefreshHeading();
        }

        private void HandleState(GameState before, GameState after)
        {
            if (next == null) return;
            next.gameObject.SetActive(after == GameState.Won);
            RefreshHeading();
        }

        public bool SelectLevel(int index)
        {
            if (index < 0 || index >= LevelCount || index > CampaignProgress.HighestUnlocked(campaign)) return false;
            PlayerPrefs.SetInt(SelectionKey, index); PlayerPrefs.Save();
            Time.timeScale = 1f;
            SceneManager.LoadScene(gameObject.scene.name);
            return true;
        }

        private void OpenMenu()
        {
            resumeAfterMenu = references.GameManager.CurrentState == GameState.Playing;
            if (resumeAfterMenu) references.GameManager.Pause();
            int unlocked = CampaignProgress.HighestUnlocked(campaign);
            for (int i = 0; i < stageButtons.Length; i++)
            {
                float best = CampaignProgress.Best(campaign, i);
                stageButtons[i].interactable = i <= unlocked;
                stageButtons[i].GetComponentInChildren<TMP_Text>().text =
                    (i + 1) + ". " + campaign.stages[i].title + "    " +
                    (i > unlocked ? "LOCKED" : new string('*', CampaignProgress.Stars(campaign, i)) +
                    "   " + (best > 0f ? best.ToString("0.00") + "s" : "Not completed"));
            }
            selector.SetActive(true);
        }

        private void CloseMenu()
        {
            selector.SetActive(false);
            if (resumeAfterMenu) references.GameManager.Resume();
            resumeAfterMenu = false;
        }

        private void RefreshHeading()
        {
            if (heading == null) return;
            var stage = campaign.stages[CurrentLevelIndex];
            heading.text = "STAGE " + (CurrentLevelIndex + 1) + "/" + LevelCount + "  " + stage.title +
                "\n" + stage.hint + "  |  Gold " + stage.goldSeconds.ToString("0") + "s";
        }

        private void BuildHud()
        {
            var canvasObject = new GameObject("CampaignUI", typeof(RectTransform), typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
            canvasObject.transform.SetParent(transform, false);
            var canvas = canvasObject.GetComponent<Canvas>(); canvas.renderMode = RenderMode.ScreenSpaceOverlay; canvas.sortingOrder = 200;
            var scaler = canvasObject.GetComponent<CanvasScaler>(); scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920, 1080); scaler.matchWidthOrHeight = .5f;
            var safe = Rect("SafeArea", canvasObject.transform, Vector2.zero, Vector2.zero);
            safe.anchorMin = Vector2.zero; safe.anchorMax = Vector2.one; safe.offsetMin = safe.offsetMax = Vector2.zero;
            canvasObject.AddComponent<SafeAreaFitter>().Configure(safe);
            heading = Text("Stage", safe, "", new Vector2(0, 65), new Vector2(1000, 86), 26);
            heading.color=new Color(.10f,.15f,.24f);
            heading.enableAutoSizing=true; heading.fontSizeMin=18; heading.fontSizeMax=26;
            heading.rectTransform.anchorMin = heading.rectTransform.anchorMax = new Vector2(.5f, 0f);
            Button("Levels", safe, "LEVELS", new Vector2(570, -65), new Vector2(260, 88), OpenMenu,
                new Vector2(0, 1));
            next = Button("Next", safe, "NEXT STAGE", new Vector2(-250, 85), new Vector2(420, 96),
                () => SelectLevel((CurrentLevelIndex + 1) % LevelCount), new Vector2(1, 0));
            next.GetComponentInChildren<TMP_Text>().text = CurrentLevelIndex == LevelCount - 1 ? "CAMPAIGN COMPLETE - REPLAY" : "NEXT STAGE";
            next.gameObject.SetActive(false);
            selector = Rect("LevelSelector", safe, Vector2.zero, Vector2.zero).gameObject;
            var sr = selector.GetComponent<RectTransform>(); sr.anchorMin = Vector2.zero; sr.anchorMax = Vector2.one;
            sr.offsetMin = sr.offsetMax = Vector2.zero;
            selector.AddComponent<Image>().color = new Color(.045f, .065f, .12f, 1f);
            Text("Title", sr, "COIN DASH / FIVE STAGE CHALLENGE", new Vector2(0, 360), new Vector2(1400, 80), 46);
            Text("Hint", sr, "Win to unlock the next route. Replay for three medals.", new Vector2(0, 285), new Vector2(1400, 60), 28);
            stageButtons = new Button[LevelCount];
            for (int i = 0; i < LevelCount; i++)
            {
                int index = i;
                stageButtons[i] = Button("Level_" + i, sr, "", new Vector2(0, 180 - i * 108), new Vector2(1150, 88), () => SelectLevel(index));
            }
            Button("Close", sr, "BACK", new Vector2(0, -420), new Vector2(380, 88), CloseMenu);
            selector.SetActive(false);
        }

        private static RectTransform Rect(string name, Transform parent, Vector2 position, Vector2 size)
        {
            var go = new GameObject(name, typeof(RectTransform));
            var rect = go.GetComponent<RectTransform>(); rect.SetParent(parent, false);
            rect.anchorMin = rect.anchorMax = rect.pivot = new Vector2(.5f, .5f);
            rect.anchoredPosition = position; rect.sizeDelta = size; return rect;
        }

        private static TMP_Text Text(string name, Transform parent, string value, Vector2 position, Vector2 size, int fontSize)
        {
            var rect = Rect(name, parent, position, size);
            var text = rect.gameObject.AddComponent<TextMeshProUGUI>(); text.font = TMP_Settings.defaultFontAsset;
            text.text = value; text.fontSize = fontSize; text.alignment = TextAlignmentOptions.Center;
            text.color = Color.white; text.raycastTarget = false; return text;
        }

        private static Button Button(string name, Transform parent, string title, Vector2 position, Vector2 size,
            UnityEngine.Events.UnityAction action, Vector2? anchor = null)
        {
            var rect = Rect(name, parent, position, size);
            if (anchor.HasValue) rect.anchorMin = rect.anchorMax = anchor.Value;
            var image = rect.gameObject.AddComponent<Image>(); image.color = new Color(.12f, .25f, .35f, 1);
            var button = rect.gameObject.AddComponent<Button>(); button.targetGraphic = image; button.onClick.AddListener(action);
            var label = Text("Label", rect, title, Vector2.zero, size - new Vector2(24, 8), 28);
            label.enableAutoSizing = true; label.fontSizeMin = 18; label.fontSizeMax = 28;
            return button;
        }
    }
}
