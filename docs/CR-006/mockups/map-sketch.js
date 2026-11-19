"use strict";

// Original, fictional cartography for the design review.
window.drawMapSketch = function drawMapSketch(canvas, options) {
  const { width, height } = canvas.getBoundingClientRect();
  const density = Math.min(window.devicePixelRatio || 1, 2);
  if (!width || !height) return;
  canvas.width = Math.round(width * density);
  canvas.height = Math.round(height * density);
  const ctx = canvas.getContext("2d");
  const satellite = options.satellite;
  const palette = satellite
    ? { land: "#616c4d", trees: "#3d543f", grass: "#506047", river: "#314b50", edge: "#789077",
        block: "#8b806d", building: "#a3aaa0", factory: "#acaba1", road: "#c9bea2",
        roadEdge: "#5a6254", trail: "#999979", text: "#f5f1db", halo: "#485646" }
    : { land: "#eeece2", trees: "#c8ddbb", grass: "#dce6cb", river: "#abcfdc", edge: "#98becb",
        block: "#e6e2d8", building: "#d2c9ba", factory: "#d1c7b7", road: "#fffdf6",
        roadEdge: "#d1cabe", trail: "#c2b9a6", text: "#647669", halo: "#f4f1e9" };
  const scale = Math.max(width / 1480, height / 920) * options.zoom;
  const focusX = width * .5;
  const focusY = height * (width < 900 ? .45 : .51);
  ctx.scale(density, density);
  ctx.fillStyle = palette.land;
  ctx.fillRect(0, 0, width, height);
  ctx.translate(focusX, focusY);
  ctx.scale(scale, scale);
  ctx.translate(-725, -458);

  let seed = 781229;
  const random = () => {
    seed = (Math.imul(1664525, seed) + 1013904223) >>> 0;
    return seed / 4294967296;
  };

  function polygon(points, fill, stroke, lineWidth = 1) {
    ctx.beginPath();
    points.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    ctx.closePath();
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lineWidth; ctx.stroke(); }
  }

  function road(points, width) {
    ctx.beginPath();
    points.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.strokeStyle = palette.roadEdge;
    ctx.lineWidth = width + 2;
    ctx.stroke();
    ctx.strokeStyle = palette.road;
    ctx.lineWidth = width;
    ctx.stroke();
  }

  function caption(label, x, y, style = {}) {
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(style.angle || 0);
    ctx.textAlign = "center";
    ctx.font = `${style.weight || 500} ${style.size || 13}px "Segoe UI", sans-serif`;
    ctx.strokeStyle = style.halo || palette.halo;
    ctx.lineWidth = 4;
    ctx.lineJoin = "round";
    ctx.strokeText(label, 0, 0);
    ctx.fillStyle = style.color || palette.text;
    ctx.fillText(label, 0, 0);
    ctx.restore();
  }

  polygon([[-500, 505], [85, 515], [230, 466], [350, 528], [414, 649], [354, 757],
    [227, 880], [235, 1300], [-500, 1300]], palette.grass);
  polygon([[823, -450], [1880, -450], [1678, 337], [1480, 356], [1240, 416], [1107, 372],
    [1008, 296], [930, 302], [876, 218]], palette.grass);
  polygon([[967, 55], [1477, 16], [1730, 170], [1499, 311], [1238, 360], [1128, 327],
    [1050, 233], [975, 223], [925, 160]], palette.trees);
  polygon([[78, 595], [222, 552], [332, 578], [376, 665], [274, 753], [137, 779], [55, 690]], palette.trees);
  polygon([[983, 586], [1173, 624], [1193, 751], [1098, 831], [1001, 772], [959, 691]], palette.trees);
  polygon([[4, -180], [321, -220], [389, 60], [257, 150], [66, 135], [-250, 38]], palette.grass);

  ctx.globalAlpha = satellite ? .20 : .17;
  for (let i = 0; i < 10500; i += 1) {
    const x = random() * 1850 - 180;
    const y = random() * 1220 - 140;
    ctx.fillStyle = random() < .48 ? palette.trees : palette.roadEdge;
    ctx.fillRect(x, y, 1 + random() * 4, 1 + random() * 3);
  }
  ctx.globalAlpha = 1;

  ctx.beginPath();
  ctx.moveTo(-450, 311);
  ctx.bezierCurveTo(-100, 330, 135, 195, 333, 295);
  ctx.bezierCurveTo(490, 382, 437, 532, 531, 634);
  ctx.bezierCurveTo(636, 744, 836, 765, 878, 945);
  ctx.bezierCurveTo(930, 1087, 758, 1164, 812, 1360);
  ctx.strokeStyle = palette.edge;
  ctx.lineWidth = 112;
  ctx.stroke();
  ctx.strokeStyle = palette.river;
  ctx.lineWidth = 108;
  ctx.stroke();

  function neighborhood(x, y, angle, cols, rows, cellW, cellH, industrial = false) {
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle);
    for (let row = 0; row < rows; row += 1) {
      for (let col = 0; col < cols; col += 1) {
        const left = col * cellW;
        const top = row * cellH;
        ctx.fillStyle = random() < .15 ? palette.grass : palette.block;
        ctx.fillRect(left + 3, top + 3, cellW - 6, cellH - 6);
        const count = industrial ? 2 : 6;
        for (let i = 0; i < count; i += 1) {
          const bx = left + 7 + random() * (cellW - (industrial ? 48 : 17));
          const by = top + 7 + random() * (cellH - 22);
          const bw = industrial ? 22 + random() * 25 : 5 + random() * 11;
          const bh = industrial ? 8 + random() * 11 : 4 + random() * 7;
          ctx.fillStyle = industrial ? palette.factory : palette.building;
          ctx.fillRect(bx, by, bw, bh);
        }
      }
    }
    for (let col = 0; col <= cols; col += 1) road([[col * cellW, 0], [col * cellW, rows * cellH]], industrial ? 3 : 2);
    for (let row = 0; row <= rows; row += 1) road([[0, row * cellH], [cols * cellW, row * cellH]], industrial ? 4 : 3);
    ctx.restore();
  }

  neighborhood(36, 78, -.14, 7, 4, 47, 33);
  neighborhood(557, 92, .17, 8, 4, 48, 39);
  neighborhood(571, 293, .09, 5, 5, 76, 54, true);
  neighborhood(939, 348, -.21, 6, 6, 54, 35);
  neighborhood(1149, 667, .31, 8, 6, 50, 34);
  neighborhood(43, 820, -.09, 9, 4, 48, 36);
  neighborhood(285, 439, .37, 4, 2, 36, 37);

  road([[-185, 399], [203, 368], [363, 412], [520, 381], [607, 367], [838, 397], [967, 375],
    [1145, 344], [1408, 370], [1790, 302]], 13);
  road([[517, -150], [527, 175], [548, 320], [553, 448], [605, 553], [683, 613], [821, 666],
    [932, 734], [1052, 872], [1090, 1110]], 11);
  road([[855, -120], [883, 160], [915, 324], [872, 440], [844, 556], [865, 619],
    [995, 685], [1105, 744], [1600, 983]], 9);
  road([[146, 1030], [331, 820], [436, 730], [528, 722], [668, 690], [807, 669], [951, 646],
    [1112, 588], [1484, 546], [1730, 570]], 12);
  road([[102, -45], [177, 117], [235, 268], [216, 369], [227, 490], [311, 588]], 8);
  road([[619, 268], [641, 379], [673, 480], [701, 585]], 7);
  road([[757, 275], [780, 381], [765, 462], [790, 562], [841, 622]], 7);

  caption("ЗАПАДНЫЙ ЛЕС", 1182, 170, { size: 15, weight: 650 });
  caption("Набережная улица", 372, 393, { angle: .03 });
  caption("Промышленная улица", 715, 383, { angle: .13 });
  caption("Промышленный район", 811, 310, { size: 16, weight: 650 });
  caption("Железнодорожная улица", 1100, 572, { angle: -.15 });
  caption("Ольховка", 465, 624, { angle: .94, size: 17, color: satellite ? "#d3eeee" : "#49798e" });
  caption("Садовый квартал", 1126, 470, { size: 15, weight: 650 });
  caption("Лесопарк", 1051, 762, { size: 15, weight: 650 });

  const parcels = [
    [[535, 360], [619, 351], [638, 386], [548, 397]],
    [[553, 402], [639, 392], [657, 449], [575, 463]],
    [[584, 475], [665, 459], [681, 511], [614, 527]],
    [[613, 538], [681, 523], [709, 584], [655, 601]],
    [[625, 318], [695, 328], [703, 365], [640, 356]],
    [[703, 328], [756, 335], [766, 374], [712, 367]],
    [[768, 335], [875, 349], [858, 390], [779, 379]],
    [[782, 391], [854, 403], [831, 444], [778, 440]],
    [[779, 449], [832, 452], [827, 495], [788, 503]],
    [[785, 514], [834, 505], [852, 544], [793, 560]],
    [[798, 571], [854, 557], [875, 598], [823, 615]],
    [[882, 383], [942, 371], [938, 418], [866, 435]],
    [[859, 448], [939, 430], [927, 477], [847, 488]],
    [[846, 500], [930, 488], [946, 539], [865, 549]],
    [[875, 560], [951, 551], [977, 588], [904, 602]],
    [[704, 548], [765, 537], [786, 591], [732, 605]]
  ];
  ctx.setLineDash([9, 8]);
  ctx.beginPath();
  ctx.ellipse(726, 464, 257, 202, .09, 0, Math.PI * 2);
  ctx.strokeStyle = satellite ? "#a9cfff" : "#1760bd";
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.setLineDash([]);

  if (options.boundaries) {
    const fills = ["#e6a65055", "#a688c84d", "#83ab544a", "#62a9bd50"];
    parcels.forEach((points, i) => polygon(points, options.fill ? fills[i % fills.length] : null, "#20B7ED", 2));
    const source = [[655, 393], [746, 398], [756, 476], [681, 492], [668, 446]];
    const selected = [[691, 510], [758, 487], [772, 519], [702, 541]];
    polygon(source, "#a855f73d", "#9333EA", 5);
    polygon(selected, "#38bdf82b", "#0284C7", 5);
    if (options.labels) {
      const sourceInk = satellite ? "#f4e1ff" : "#562991";
      const selectedInk = satellite ? "#d9f2ff" : "#075680";
      const parcelInk = satellite ? "#dcf6ff" : "#0876a0";
      const parcelHalo = satellite ? "#233632" : palette.halo;
      caption("63:09:0102159:100", 706, 442, { size: 12, weight: 700, color: sourceInk, halo: parcelHalo });
      caption("63:09:0102159:101", 737, 523, { size: 11, weight: 700, color: selectedInk, halo: parcelHalo });
      caption("…:128", 804, 427, { size: 11, color: parcelInk, halo: parcelHalo });
      caption("…:756", 617, 428, { size: 11, color: parcelInk, halo: parcelHalo });
      caption("…:298", 899, 462, { size: 11, color: parcelInk, halo: parcelHalo });
    }
    ctx.beginPath();
    ctx.moveTo(722, 483);
    ctx.lineTo(727, 498);
    ctx.strokeStyle = "#DC2626";
    ctx.lineWidth = 3.2;
    ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.setLineDash([]);
    for (const [x, y] of [[722, 483], [727, 498]]) {
      ctx.beginPath();
      ctx.arc(x, y, 3.7, 0, 7);
      ctx.fillStyle = "#DC2626";
      ctx.fill();
      ctx.strokeStyle = "white";
      ctx.lineWidth = 1.4;
      ctx.stroke();
    }
    caption("24,6 м", 758, 481, { size: 13, weight: 700, color: "#a61f26", halo: "#fff" });
  }

  if (options.osm) {
    for (const [x, y, color] of [[626, 373, "#9866c4"], [799, 405, "#64748b"], [906, 502, "#3585bd"],
      [875, 553, "#45966a"], [705, 450, "#9866c4"], [951, 406, "#b6814c"], [593, 470, "#4d929d"]]) {
      ctx.beginPath();
      ctx.arc(x, y, 6, 0, 7);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = "white";
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  }
};
