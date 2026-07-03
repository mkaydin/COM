---
type: Reference
resource: council://document/dccf5957-b01a-438e-85eb-879f60e5f308
title: '[Divination] what is neural network [chunk 27]'
description: 'ed example of training a neural network in object detection: The network
  is trained by multiple images that are known to depict starfish and sea urchins
  , which'
tags:
- divination_chunk
timestamp: '2026-07-02T06:57:55+00:00'
---

ed example of training a neural network in object detection: The network is trained by multiple images that are known to depict
starfish
and
sea urchins
, which are correlated with "nodes" that represent visual
features
. The starfish match with a ringed texture and a star outline, whereas most sea urchins match with a striped texture and oval shape. However, the instance of a ring textured sea urchin creates a weakly weighted association between them.
Subsequent run of the network on an input image:
[
5
]
The network correctly detects the starfish. However, the weakly weighted association between ringed texture and sea urchin also confers a weak signal to the latter from one of two intermediate nodes. In addition, a shell that was not included in the training gives a weak signal for the oval shape, also resulting in a weak signal for the sea urchin output. These weak signals may result in a
false positive
result for sea urchin.
In reality, textures and outlines would not be represente
