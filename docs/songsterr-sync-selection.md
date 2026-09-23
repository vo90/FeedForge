# Primary recording timing selection

The public Songsterr client captured on 23 September 2026 selects its primary
video (`!feature`) in ordinary `video/load` and `video/switchType(main)` paths.
Alternative recordings are a separate fallback path. An alternative can have
the same video ID but a different, older measure map.

Observed example: Chop Suey!, song 3708, approved revision 9044289, recording
MlcJQYON2Go. Primary entry 5810473 has 111 points; alternative entry 15636 has
113. Comparing these as equal candidates incorrectly makes the primary map
unavailable. No recording or source revision is changed by selecting primary.

Primary evidence: the public `common-z7xLi0BiPF1hudTP.js` asset, SHA-256
039a95156a0b966e7c3602ea8d0f4ae403266ec2f7ba930dd64fcbe41151fc29,
and anonymous `/api/video-points/3708/9044289/list`. The previously captured
`common-DWZ1FVtGLtYGNv20.js` implements the same precedence.

The importer keeps exact song/revision/video identity, rejects track-specific
and problematic maps, and validates all selected points. Conflicting primary
maps still require local matching; response order is not a tie breaker. An
invalid primary never silently falls back to an alternative for that video.
The bounded anonymous response is retained as selection evidence. The worker's
independent note/audio bounds checks remain unchanged.
