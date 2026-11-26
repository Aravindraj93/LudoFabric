using System;
using CoinDash.Campaign;
using CoinDash.Level;
using LudoFabric.Runtime;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace CoinDash.EditorTools
{
    public static class CampaignUpgradeBuilder
    {
        public const string CatalogPath = "Assets/Game/Settings/CoinDashCampaign.asset";

        [MenuItem("Coin Dash/Build Five Stage Campaign")]
        public static void Build()
        {
            var scene = EditorSceneManager.OpenScene(CoinDashArenaBuilder.ScenePath);
            var refs = UnityEngine.Object.FindFirstObjectByType<SceneReferences>();
            if (refs == null) throw new InvalidOperationException("Build the base arena before its campaign.");
            var catalog = AssetDatabase.LoadAssetAtPath<CampaignDefinition>(CatalogPath);
            if (catalog == null)
            {
                catalog = ScriptableObject.CreateInstance<CampaignDefinition>();
                AssetDatabase.CreateAsset(catalog, CatalogPath);
            }
            catalog.campaignId = "CoinDash.Campaign.v2";
            catalog.stages = new[]
            {
                Stage("first-steps", "First Steps", "Read the route. Learn the corners.", 60, 20, 32,
                    new[] {P(-8,-4),P(-8,0),P(-8,4),P(-4,5.5f),P(0,5.5f),P(5,5.5f),P(8,3),P(8,-2),P(5,-5.5f),P(0,-5.5f)},
                    new[] {O(0,0,4,1.2f),O(4,2,1.5f,1.5f),O(-2,-2.5f,3,1)}, new Color(.40f,.85f,.72f)),
                Stage("switchbacks", "Switchback Alley", "Alternate sides around three chicanes.", 65, 25, 40,
                    new[] {P(-8,-5),P(6,-5),P(9,-2),P(1,-1.5f),P(-8,-1.5f),P(-9,2),P(0,2),P(8,2),P(8,5.5f),P(-7,5.5f)},
                    new[] {O(-3,-3.2f,12,.8f),O(3,.3f,12,.8f),O(-3,3.8f,12,.8f)}, new Color(.43f,.73f,.91f)),
                Stage("inner-orbit", "Inner Orbit", "Circle the islands, then cut through the centre.", 60, 23, 37,
                    new[] {P(-8,-4),P(-8,1),P(-6,5),P(0,5.5f),P(6,5),P(8,1),P(8,-4),P(3,-5.5f),P(-3,-5.5f),P(0,0)},
                    new[] {O(-4,0,1.4f,6),O(4,0,1.4f,6),O(0,3,3,1)}, new Color(.78f,.64f,.90f)),
                Stage("cross-traffic", "Cross Traffic", "Watch the sliding gates. Time each crossing.", 65, 26, 42,
                    new[] {P(-9,-4),P(-9,1),P(-7,5.5f),P(-2,5.5f),P(3,5.5f),P(8,4),P(9,-1),P(7,-5.5f),P(1,-5.5f),P(-4,-5.5f)},
                    new[] {O(-5,0,1.5f,2),O(5,0,1.5f,2),O(0,0,1.5f,1.5f),
                        O(-2,2.8f,3,.6f,new Vector3(3,0,0),1.2f),O(2,-2.8f,3,.6f,new Vector3(3,0,0),1.5f)}, new Color(.96f,.74f,.48f)),
                Stage("grand-circuit", "Grand Circuit", "Combine cornering, route planning and gate timing.", 70, 30, 48,
                    new[] {P(-9,-4),P(-9,2),P(-7,5.5f),P(-1,5.5f),P(5,5.5f),P(9,3),P(9,-2),P(7,-5.5f),P(1,-5.5f),P(-4,-5.5f)},
                    new[] {O(-4,0,1,5),O(4,0,1,5),O(0,2.6f,3,.7f),O(0,-2.6f,3,.7f),
                        O(-7,0,1.6f,.6f,new Vector3(0,0,2.5f),1.3f),O(7,0,1.6f,.6f,new Vector3(0,0,2.5f),1.6f)}, new Color(.55f,.81f,.91f))
            };
            catalog.Validate();
            foreach (var level in catalog.stages) ValidateGeometry(level);
            var controller = UnityEngine.Object.FindFirstObjectByType<CampaignController>();
            if (controller == null) controller = new GameObject("CoinDashCampaign").AddComponent<CampaignController>();
            controller.Configure(catalog, refs);
            foreach (var obstacle in refs.Obstacles) GameObjectUtility.SetStaticEditorFlags(obstacle, 0);
            EditorUtility.SetDirty(controller); EditorUtility.SetDirty(catalog);
            EditorSceneManager.MarkSceneDirty(scene); EditorSceneManager.SaveScene(scene); AssetDatabase.SaveAssets();
            Debug.Log("Built and validated five distinct campaign stages; the original arena remains the reusable scene shell.");
        }

        private static Vector3 P(float x, float z) => new Vector3(x, .72f, z);
        private static ObstacleDefinition O(float x, float z, float width, float depth, Vector3 motion = default, float speed = 1f)
            => new ObstacleDefinition { position = new Vector3(x,.65f,z), size = new Vector3(width,1.3f,depth), motion = motion, speed = speed };
        private static StageDefinition Stage(string id, string title, string hint, float seconds, float gold, float silver,
            Vector3[] coins, ObstacleDefinition[] obstacles, Color color)
            => new StageDefinition { id = id, title = title, hint = hint, seconds = seconds, goldSeconds = gold,
                silverSeconds = silver, spawn = new Vector3(-10,.55f,-6), coins = coins, obstacles = obstacles, floorColor = color };

        public static void ValidateGeometry(StageDefinition stage)
        {
            if (stage.coins.Length != 10) throw new InvalidOperationException(stage.id + ": scene pool requires ten coins.");
            for (int i=0;i<stage.coins.Length;i++)
            {
                Vector3 point=stage.coins[i];
                if (Mathf.Abs(point.x)>10.8f || Mathf.Abs(point.z)>6.8f) throw new InvalidOperationException(stage.id+": coin outside playable bounds.");
                for (int j=0;j<i;j++) if ((point-stage.coins[j]).sqrMagnitude<1f) throw new InvalidOperationException(stage.id+": overlapping coins.");
                foreach (var wall in stage.obstacles)
                    if (Mathf.Abs(point.x-wall.position.x)<wall.size.x*.5f+.6f && Mathf.Abs(point.z-wall.position.z)<wall.size.z*.5f+.6f)
                        throw new InvalidOperationException(stage.id+": a coin starts inside an obstacle.");
            }
            // Conservative ball-clearance flood fill: static walls must not seal off a coin.
            const int width=45, height=29;
            var visited=new bool[width,height];
            var queue=new System.Collections.Generic.Queue<Vector2Int>();
            var start=new Vector2Int(Mathf.RoundToInt((stage.spawn.x+11)*2),Mathf.RoundToInt((stage.spawn.z+7)*2));
            queue.Enqueue(start); visited[start.x,start.y]=true;
            int[] dx={1,-1,0,0}, dz={0,0,1,-1};
            while(queue.Count>0)
            {
                var point=queue.Dequeue();
                for(int direction=0;direction<4;direction++)
                {
                    int x=point.x+dx[direction],z=point.y+dz[direction];
                    if(x<0||z<0||x>=width||z>=height||visited[x,z]) continue;
                    float wx=x*.5f-11,wz=z*.5f-7;
                    bool blocked=false;
                    foreach(var wall in stage.obstacles)
                        if(wall.motion.sqrMagnitude==0 && Mathf.Abs(wx-wall.position.x)<wall.size.x*.5f+.55f &&
                           Mathf.Abs(wz-wall.position.z)<wall.size.z*.5f+.55f) {blocked=true;break;}
                    if(blocked) continue;
                    visited[x,z]=true; queue.Enqueue(new Vector2Int(x,z));
                }
            }
            foreach(var coin in stage.coins)
                if(!visited[Mathf.RoundToInt((coin.x+11)*2),Mathf.RoundToInt((coin.z+7)*2)])
                    throw new InvalidOperationException(stage.id+": a coin has no static-clearance route from spawn.");
        }
    }
}
