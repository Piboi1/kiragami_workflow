design_45deg_r13_c10: test chute for the "45 degree ribbon" theory

Pattern (inches / degrees):
  13 rings, 10 cuts per ring, ring spacing exponent 1.0 (equal ring pitch 0.333 in on every ring), inner radius 0.75 in, outer radius 4.75 in, 10 in diameter
  overlap: 10.0 deg on rings 1-10, 12.0 deg on rings 11-13 (inner 10 / outer 12: inside the new ranges inner 7.8-10 and outer 7.8-12)
  gaps 8 deg (rings 1-10) and 6 deg (rings 11-13); no ring clamped by the generator

Why these numbers
  - 13 rings with equal pitch: a ribbon at 45 deg needs depth per ring = pitch. Depth per ring is about 0.346 in (18-ring chute: 5.886 in / 17 rings), the pitch here is 0.333 in -> predicted slope 46 deg (44-51 deg over the measured range of depth per ring 0.317-0.418 in).
  - 10 cuts: shortest ribbons that keep the requested overlap (12 cuts would be clamped to 9 deg); median cut length / pitch is 3.8 (4 cuts: 7.4).
  - Ring 1: overlap arc / pitch = 0.39, above the opening threshold seen so far (in10 0.15 did not open, ov12 0.30 and in25 0.37 opened); outermost rings 2.6-2.8, in the plateau range.
  - Width = length cannot hold on every ring (cut length 0.37 in at ring 1 to 2.5 in at ring 13 against a pitch of 0.333 in); it is square only at ring 1.

What to measure
  - STRETCH HEIGHT of the cut chute. Predicted 10.5 cm (4.2 in; range 9.7-12.7 cm); outer radius is 12.1 cm. Slope from horizontal = atan(stretch height per ring / 0.333 in), with stretch height per ring = stretch height / 12. About 10-11 cm means about 45 deg.
  - Drop it indoors at 13 ft in the same session as in78 (uniform 7.8 deg, 28 rings, 1.194 s) and one more plateau chute, 5-10 trials each, interleaved.
  - Support for the theory: a time clearly above the 28-ring plateau (more than about 0.05 s); worse than that means the shallow dome loses.
