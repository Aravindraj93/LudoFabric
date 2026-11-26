using CoinDash.Core;
using CoinDash.GameFlow;
using UnityEngine;

namespace CoinDash.Campaign
{
    public sealed class MovingGate : MonoBehaviour
    {
        private Rigidbody body;
        private CoinDashGameManager manager;
        private Vector3 origin, motion;
        private float speed, phase;

        public void Configure(CoinDashGameManager manager, Vector3 motion, float speed)
        {
            this.manager = manager; this.motion = motion; this.speed = speed;
            origin = transform.position;
            body = GetComponent<Rigidbody>();
            if (body == null) body = gameObject.AddComponent<Rigidbody>();
            body.useGravity = false; body.isKinematic = true; body.interpolation = RigidbodyInterpolation.Interpolate;
        }

        public void ResetMotion() { phase = 0f; body.position = origin; }

        private void FixedUpdate()
        {
            if (manager == null || manager.CurrentState != GameState.Playing) return;
            phase += Time.fixedDeltaTime * speed;
            body.MovePosition(origin + motion * Mathf.Sin(phase));
        }
    }
}
