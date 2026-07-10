from __future__ import annotations

import unittest

from timiniprint.protocol.plan import ProtocolPlan
from timiniprint.protocol.steps import ProtocolReplyExpectation, ProtocolStep


class ProtocolPlanTests(unittest.TestCase):
    def test_stream_normalizes_payload(self) -> None:
        plan = ProtocolPlan.stream(bytearray(b"stream"))

        self.assertEqual(plan.payload, b"stream")
        self.assertEqual(plan.steps, ())

    def test_sequence_derives_fallback_payload(self) -> None:
        steps = (
            ProtocolStep.send("setup", b"A"),
            ProtocolStep.query(
                "status",
                b"S",
                expect=ProtocolReplyExpectation.STATUS_ZERO,
                include_in_payload=False,
            ),
            ProtocolStep.send("bitmap", b"B"),
        )

        plan = ProtocolPlan.sequence(steps)

        self.assertEqual(plan.payload, b"AB")
        self.assertEqual(plan.steps, steps)

    def test_rejects_payload_that_disagrees_with_steps(self) -> None:
        with self.assertRaisesRegex(ValueError, "protocol steps"):
            ProtocolPlan(
                payload=b"A",
                steps=(ProtocolStep.send("bitmap", b"B"),),
            )


if __name__ == "__main__":
    unittest.main()
