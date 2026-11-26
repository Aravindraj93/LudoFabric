using System;
using UnityEngine;

namespace LudoFabric.Runtime
{
    [Serializable]
    public sealed class ObstacleDefinition
    {
        public Vector3 position;
        public Vector3 size = Vector3.one;
        public float yaw;
        public Vector3 motion = Vector3.zero;
        public float speed = 1f;
    }

    [Serializable]
    public sealed class StageDefinition
    {
        public string id;
        public string title;
        public string hint;
        public float seconds = 60f;
        public float goldSeconds = 25f;
        public float silverSeconds = 40f;
        public Vector3 spawn;
        public Vector3[] coins = Array.Empty<Vector3>();
        public ObstacleDefinition[] obstacles = Array.Empty<ObstacleDefinition>();
        public Color floorColor = new Color(0.4f, 0.85f, 0.72f);

        public int Medal(float elapsed)
        {
            if (float.IsNaN(elapsed) || float.IsInfinity(elapsed) || elapsed <= 0f) return 0;
            return elapsed <= goldSeconds ? 3 : elapsed <= silverSeconds ? 2 : 1;
        }
    }

    [CreateAssetMenu(menuName = "LudoFabric/Campaign", fileName = "Campaign")]
    public sealed class CampaignDefinition : ScriptableObject
    {
        public string campaignId = "campaign.v1";
        public StageDefinition[] stages = Array.Empty<StageDefinition>();

        public void Validate()
        {
            if (string.IsNullOrWhiteSpace(campaignId) || stages == null || stages.Length == 0)
                throw new InvalidOperationException("A campaign needs a stable ID and at least one stage.");
            var ids = new System.Collections.Generic.HashSet<string>();
            foreach (var stage in stages)
            {
                if (stage == null || string.IsNullOrWhiteSpace(stage.id) || !ids.Add(stage.id))
                    throw new InvalidOperationException("Every stage needs a unique stable ID.");
                if (float.IsNaN(stage.seconds) || float.IsInfinity(stage.seconds) || stage.seconds <= 0f ||
                    float.IsNaN(stage.goldSeconds) || float.IsInfinity(stage.goldSeconds) ||
                    float.IsNaN(stage.silverSeconds) || float.IsInfinity(stage.silverSeconds) ||
                    stage.goldSeconds <= 0f || stage.silverSeconds < stage.goldSeconds || stage.seconds < stage.silverSeconds)
                    throw new InvalidOperationException("Stage medal times must be ordered within a finite time budget.");
                if (stage.coins == null) stage.coins = Array.Empty<Vector3>();
                if (stage.obstacles == null) stage.obstacles = Array.Empty<ObstacleDefinition>();
            }
        }
    }
}
